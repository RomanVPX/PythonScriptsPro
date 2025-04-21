import hashlib
import logging
import traceback
import time
import datetime

import voluptuous as vol
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.json import JSON_DUMP
from homeassistant.helpers.typing import ConfigType
from homeassistant.requirements import async_process_requirements
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

DOMAIN = "python_script"
CONF_REQUIREMENTS = "requirements"

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Optional(CONF_REQUIREMENTS): cv.ensure_list,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)

SERVICE_SCHEMA = vol.Schema(
    {
        vol.Optional("file"): str,
        vol.Optional("source"): str,
        vol.Optional("cache"): bool,
    },
    extra=vol.ALLOW_EXTRA,
)


def md5(data: str) -> str:
    return hashlib.md5(data.encode()).hexdigest()


async def async_setup(hass: HomeAssistant, hass_config: ConfigType):
    config: dict = hass_config[DOMAIN]
    if CONF_REQUIREMENTS in config:
        hass.async_create_task(
            async_process_requirements(hass, DOMAIN, config[CONF_REQUIREMENTS])
        )

    cache_code = {}

    def handler(call: ServiceCall) -> ServiceResponse:
        # Run with SyncWorker
        file = call.data.get("file")
        srcid = md5(call.data["source"]) if "source" in call.data else None
        cache = call.data.get("cache", True)

        if not (file or srcid):
            _LOGGER.error("Either file or source is required in params")
            return

        code = cache_code.get(file or srcid)

        if not cache or not code:
            if file:
                _LOGGER.debug("Load code from file")

                file = hass.config.path(file)
                with open(file, encoding="utf-8") as f:
                    code = compile(f.read(), file, "exec")

                if cache:
                    cache_code[file] = code

            else:
                _LOGGER.debug("Load inline code")

                code = compile(call.data["source"], "<string>", "exec")

                if cache:
                    cache_code[srcid] = code

        else:
            _LOGGER.debug("Load code from cache")

        return execute_script(hass, call.data, call.context, _LOGGER, code)

    hass.services.async_register(
        DOMAIN,
        "exec",
        handler,
        SERVICE_SCHEMA,
        SupportsResponse.OPTIONAL,
    )

    return True


def execute_script(hass, data, context, logger, code) -> ServiceResponse:
    """Executes the Python script and returns the 'output' dictionary."""
    try:
        _LOGGER.debug("Run python script")

        # 1. Готовим изолированный namespace для скрипта
        script_vars = {
            "hass": hass,
            "data": data,
            "logger": logger,
            "time": time,
            "datetime": datetime,
            "dt_util": dt_util,
            "output": {}
        }

        # 2. Выполняем код в этом namespace
        exec(code, script_vars)

        # 3. Извлекаем результат ИСКЛЮЧИТЕЛЬНО из 'output'
        response = script_vars.get("output")

        # 4. Проверяем, что 'output' - это словарь, и возвращаем его
        if isinstance(response, dict):
            # Убедимся, что его можно сериализовать (опционально, но безопасно)
            try:
                JSON_DUMP(response)
                return response
            except TypeError as json_err:
                _LOGGER.error(f"Script output dictionary is not JSON serializable: {json_err}", exc_info=True)
                # Возвращаем ошибку или пустой словарь? Лучше ошибку.
                return {"error": "Script output is not JSON serializable", "details": str(json_err)}
        else:
            _LOGGER.warning(f"Script finished, but 'output' is not a dictionary (type: {type(response)}). Returning empty result.")
            # Если output не словарь, возвращаем пустой словарь или None
            return {} # Пустой словарь безопаснее

    except Exception as e:
        _LOGGER.error("Error executing Python script", exc_info=e)
        return {"error": str(e), "traceback": "".join(traceback.format_exception(e))}


# Уже не нужно, но оставлю на всякий случай
def simple_type(value) -> bool:
    """Can be converted to JSON."""
    # https://github.com/AlexxIT/PythonScriptsPro/issues/26
    if value is None or isinstance(value, (str, int, float, bool)):
        return True

    if isinstance(value, (dict, list)):
        try:
            return JSON_DUMP(value) is not None
        except TypeError:
            pass

    return False
