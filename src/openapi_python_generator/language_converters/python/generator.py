from typing import Optional

from openapi_pydantic.v3.v3_0 import OpenAPI

from openapi_python_generator.common import PydanticVersion
from openapi_python_generator.language_converters.python import common
from openapi_python_generator.language_converters.python.api_config_generator import (
    generate_api_config,
)
from openapi_python_generator.language_converters.python.model_generator import (
    generate_models,
)
from openapi_python_generator.language_converters.python.service_generator import (
    generate_services,
)
from openapi_python_generator.models import ConversionResult
from openapi_python_generator.models import LibraryConfig


def generator(
    data: OpenAPI,
    library_config: LibraryConfig,
    env_token_name: Optional[str] = None,
    use_orjson: bool = False,
    use_awaredatetime: bool = False,
    custom_template_path: Optional[str] = None,
    pydantic_version: PydanticVersion = PydanticVersion.V2,
) -> ConversionResult:
    """
    Generate Python code from an OpenAPI 3.0 specification.
    """
    if use_awaredatetime and pydantic_version != PydanticVersion.V2:
        raise ValueError("Timezone-aware datetime is only supported with Pydantic v2. Please use --pydantic-version v2.")

    common.set_use_orjson(use_orjson)
    common.set_custom_template_path(custom_template_path)
    common.set_pydantic_version(pydantic_version)
    common.set_pydantic_use_awaredatetime(use_awaredatetime)

    if data.components is not None:
        models = generate_models(data.components, pydantic_version)
    else:
        models = []

    if data.paths is not None:
        services = generate_services(data.paths, library_config)
    else:
        services = []

    api_config = generate_api_config(data, env_token_name, pydantic_version)

    return ConversionResult(
        models=models,
        services=services,
        api_config=api_config,
    )
