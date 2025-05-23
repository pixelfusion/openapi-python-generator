from pathlib import Path
from typing import List
from typing import Optional
from typing import Union

import black
import click
import httpx
import isort
import orjson
import yaml
from black import NothingChanged
from httpx import ConnectError
from httpx import ConnectTimeout
from openapi_pydantic.v3.v3_0 import OpenAPI
from pydantic import ValidationError

from .common import FormatOptions, Formatter, HTTPLibrary, PydanticVersion
from .common import library_config_dict
from .language_converters.python.generator import generator
from .language_converters.python.jinja_config import SERVICE_TEMPLATE
from .language_converters.python.jinja_config import create_jinja_env
from .models import ConversionResult


def write_code(path: Path, content: str, formatter: Formatter) -> None:
    """
    Write the content to the file at the given path.
    :param path: The path to the file.
    :param content: The content to write.
    :param formatter: The formatter applied to the code written.
    """
    if formatter == Formatter.BLACK:
        formatted_contend = format_using_black(content)
    elif formatter == Formatter.NONE:
        formatted_contend = content
    else:
        raise NotImplementedError(f"Missing implementation for formatter {formatter!r}.")
    with open(path, "w") as f:
        f.write(formatted_contend)


def format_using_black(content: str) -> str:
    try:
        formatted_contend = black.format_file_contents(
            content, fast=FormatOptions.skip_validation, mode=black.FileMode(line_length=FormatOptions.line_length)
        )
    except NothingChanged:
        return content
    return isort.code(formatted_contend, line_length=FormatOptions.line_length)


def get_open_api(source: Union[str, Path]) -> OpenAPI:
    """
    Tries to fetch the openapi specification file from the web or load from a local file.
    Supports both JSON and YAML formats. Returns the according OpenAPI object.

    Args:
        source: URL or file path to the OpenAPI specification

    Returns:
        OpenAPI: Parsed OpenAPI specification object

    Raises:
        FileNotFoundError: If the specified file cannot be found
        ConnectError: If the URL cannot be accessed
        ValidationError: If the specification is invalid
        JSONDecodeError/YAMLError: If the file cannot be parsed
    """
    try:
        # Handle remote files
        if not isinstance(source, Path) and (
                source.startswith("http://") or source.startswith("https://")
        ):
            content = httpx.get(source).text
            # Try JSON first, then YAML for remote files
            try:
                return OpenAPI(**orjson.loads(content))
            except orjson.JSONDecodeError:
                return OpenAPI(**yaml.safe_load(content))

        # Handle local files
        with open(source, "r") as f:
            file_content = f.read()

            # Try JSON first
            try:
                return OpenAPI(**orjson.loads(file_content))
            except orjson.JSONDecodeError:
                # If JSON fails, try YAML
                try:
                    return OpenAPI(**yaml.safe_load(file_content))
                except yaml.YAMLError as e:
                    click.echo(
                        f"File {source} is neither a valid JSON nor YAML file: {str(e)}"
                    )
                    raise

    except FileNotFoundError:
        click.echo(
            f"File {source} not found. Please make sure to pass the path to the OpenAPI specification."
        )
        raise
    except (ConnectError, ConnectTimeout):
        click.echo(f"Could not connect to {source}.")
        raise ConnectError(f"Could not connect to {source}.") from None
    except ValidationError:
        click.echo(
            f"File {source} is not a valid OpenAPI 3.0 specification."
        )
        raise


def write_data(data: ConversionResult, output: Union[str, Path], formatter: Formatter) -> None:
    """
    This function will firstly create the folder structure of output, if it doesn't exist. Then it will create the
    models from data.models into the models sub module of the output folder. After this, the services will be created
    into the services sub module of the output folder.
    :param data: The data to write.
    :param output: The path to the output folder.
    :param formatter: The formatter applied to the code written.
    """

    # Create the folder structure of the output folder.
    Path(output).mkdir(parents=True, exist_ok=True)

    # Create the models module.
    models_path = Path(output) / "models"
    models_path.mkdir(parents=True, exist_ok=True)

    # Create the services module.
    services_path = Path(output) / "services"
    services_path.mkdir(parents=True, exist_ok=True)

    files: List[str] = []

    # Write the models.
    for model in data.models:
        files.append(model.file_name)
        write_code(models_path / f"{model.file_name}.py", model.content, formatter)

    # Create models.__init__.py file containing imports to all models.
    write_code(
        models_path / "__init__.py",
        "\n".join([f"from .{file} import *" for file in files]),
        formatter,
    )

    files = []

    # Write the services.
    jinja_env = create_jinja_env()
    for service in data.services:
        if len(service.operations) == 0:
            continue
        files.append(service.file_name)
        write_code(
            services_path / f"{service.file_name}.py",
            jinja_env.get_template(SERVICE_TEMPLATE).render(**service.dict()),
            formatter,
        )

    # Create services.__init__.py file containing imports to all services.
    write_code(services_path / "__init__.py", "", formatter)

    # Write the api_config.py file.
    write_code(Path(output) / "api_config.py", data.api_config.content, formatter)

    # Write the __init__.py file.
    write_code(
        Path(output) / "__init__.py",
        "from .models import *\nfrom .services import *\nfrom .api_config import *",
        formatter,
    )


def generate_data(
    source: Union[str, Path],
    output: Union[str, Path],
    library: Optional[HTTPLibrary] = HTTPLibrary.httpx,
    env_token_name: Optional[str] = None,
    use_orjson: bool = False,
    use_awaredatetime: bool = False,
    custom_template_path: Optional[str] = None,
    pydantic_version: PydanticVersion = PydanticVersion.V2,
    formatter: Formatter = Formatter.BLACK,
) -> None:
    """
    Generate Python code from an OpenAPI 3.0 specification.
    """
    data = get_open_api(source)
    click.echo(f"Generating data from {source}")

    result = generator(
        data,
        library_config_dict[library],
        env_token_name,
        use_orjson,
        use_awaredatetime,
        custom_template_path,
        pydantic_version,
    )

    write_data(result, output, formatter)
