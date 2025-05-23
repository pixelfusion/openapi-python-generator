import itertools
import re
from typing import List
from typing import Optional

import click
from openapi_pydantic.v3.v3_0 import Schema, Reference, Components

from openapi_python_generator.common import PydanticVersion
from openapi_python_generator.language_converters.python import common
from openapi_python_generator.language_converters.python.jinja_config import (
    ENUM_TEMPLATE, MODELS_TEMPLATE_PYDANTIC_V2,
)
from openapi_python_generator.language_converters.python.jinja_config import (
    MODELS_TEMPLATE,
)
from openapi_python_generator.language_converters.python.jinja_config import (
    create_jinja_env,
)
from openapi_python_generator.models import Model
from openapi_python_generator.models import Property
from openapi_python_generator.models import TypeConversion
from openapi_python_generator.models import ParentModel


def type_converter(  # noqa: C901
        schema: Schema,
        required: bool = False,
        model_name: Optional[str] = None,
) -> TypeConversion:
    """
    Converts an OpenAPI type to a Python type.
    :param schema: Schema containing the type to be converted
    :param model_name: Name of the original model on which the type is defined
    :param required: Flag indicating if the type is required by the class
    :return: The converted type
    """
    if required:
        pre_type = ""
        post_type = ""
    else:
        pre_type = "Optional["
        post_type = "]"

    original_type = schema.type.value if schema.type is not None else "object"
    import_types: Optional[List[str]] = None

    if schema.allOf is not None:
        conversions = []
        for sub_schema in schema.allOf:
            if isinstance(sub_schema, Schema):
                conversions.append(type_converter(sub_schema, True))
            else:
                import_type = common.normalize_symbol(sub_schema.ref.split("/")[-1])
                if import_type == model_name:
                    conversions.append(
                        TypeConversion(
                            original_type=sub_schema.ref,
                            converted_type='"' + model_name + '"',
                            import_types=None,
                        )
                    )
                else:
                    import_types = [f"from .{import_type} import {import_type}"]
                    conversions.append(
                        TypeConversion(
                            original_type=sub_schema.ref,
                            converted_type=import_type,
                            import_types=import_types,
                        )
                    )

        original_type = (
                "tuple<" + ",".join([i.original_type for i in conversions]) + ">"
        )
        if len(conversions) == 1:
            converted_type = conversions[0].converted_type
        else:
            converted_type = (
                    "Tuple[" + ",".join([i.converted_type for i in conversions]) + "]"
            )

        converted_type = pre_type + converted_type + post_type
        import_types = [
            i.import_types[0] for i in conversions if i.import_types is not None
        ]

    elif schema.oneOf is not None or schema.anyOf is not None:
        used = schema.oneOf if schema.oneOf is not None else schema.anyOf
        used = used if used is not None else []
        conversions = []
        for sub_schema in used:
            if isinstance(sub_schema, Schema):
                conversions.append(type_converter(sub_schema, True))
            else:
                import_type = common.normalize_symbol(sub_schema.ref.split("/")[-1])
                import_types = [f"from .{import_type} import {import_type}"]
                conversions.append(
                    TypeConversion(
                        original_type=sub_schema.ref,
                        converted_type=import_type,
                        import_types=import_types,
                    )
                )
        original_type = (
                "union<" + ",".join([i.original_type for i in conversions]) + ">"
        )

        if len(conversions) == 1:
            converted_type = conversions[0].converted_type
        else:
            converted_type = (
                    "Union[" + ",".join([i.converted_type for i in conversions]) + "]"
            )

        converted_type = pre_type + converted_type + post_type
        import_types = list(
            itertools.chain(
                *[i.import_types for i in conversions if i.import_types is not None]
            )
        )
    # With custom string format fields, in order to cast these to strict types (e.g. date, datetime, UUID)
    # orjson is required for JSON serialiation.
    elif (
        schema.type == "string"
        and schema.schema_format is not None
        and schema.schema_format.startswith("uuid")
        # orjson and pydantic v2 both support UUID
        and (common.get_use_orjson() or common.get_pydantic_version() == PydanticVersion.V2)
    ):
        if len(schema.schema_format) > 4 and schema.schema_format[4].isnumeric():
            uuid_type = schema.schema_format.upper()
            converted_type = pre_type + uuid_type + post_type
            import_types = ["from pydantic import " + uuid_type]
        else:
            converted_type = pre_type + "UUID" + post_type
            import_types = ["from uuid import UUID"]
    elif (
        schema.type == "string"
        and schema.schema_format == "date-time"
        # orjson and pydantic v2 both support datetime
        and (common.get_use_orjson() or common.get_pydantic_version() == PydanticVersion.V2)
    ):
        if common.get_pydantic_use_awaredatetime():
            converted_type = pre_type + "AwareDatetime" + post_type
            import_types = ["from pydantic import AwareDatetime"]
        else:
            converted_type = pre_type + "datetime" + post_type
            import_types = ["from datetime import datetime"]
    elif (
        schema.type == "string"
        and schema.schema_format == "date"
        # orjson and pydantic v2 both support date
        and (common.get_use_orjson() or common.get_pydantic_version() == PydanticVersion.V2)
    ):
        converted_type = pre_type + "date" + post_type
        import_types = ["from datetime import date"]
    elif (
        schema.type == "string"
        and schema.schema_format == "decimal"
        # orjson does not support Decimal
        # See https://github.com/ijl/orjson/issues/444
        and not common.get_use_orjson()
        # pydantic v2 supports Decimal
        and common.get_pydantic_version() == PydanticVersion.V2
    ):
        converted_type = pre_type + "Decimal" + post_type
        import_types = ["from decimal import Decimal"]
    elif schema.type == "string":
        converted_type = pre_type + "str" + post_type
    elif schema.type == "integer":
        converted_type = pre_type + "int" + post_type
    elif schema.type == "number":
        converted_type = pre_type + "float" + post_type
    elif schema.type == "boolean":
        converted_type = pre_type + "bool" + post_type
    elif schema.type == "array":
        retVal = pre_type + "List["
        if isinstance(schema.items, Reference):
            converted_reference = _generate_property_from_reference(
                model_name, "", schema.items, schema, required
            )
            import_types = converted_reference.type.import_types
            original_type = "array<" + converted_reference.type.original_type + ">"
            retVal += converted_reference.type.converted_type
        elif isinstance(schema.items, Schema):
            original_type = "array<" + (
                str(schema.items.type.value) if schema.items.type is not None else "unknown") + ">"
            items_type = type_converter(schema.items, True)
            import_types = items_type.import_types
            retVal += items_type.converted_type
        else:
            original_type = "array<unknown>"
            retVal += "Any"

        converted_type = retVal + "]" + post_type
    elif schema.type == "object":
        converted_type = pre_type + "Dict[str, Any]" + post_type
    elif schema.type == "null":
        converted_type = pre_type + "None" + post_type
    elif schema.type is None:
        converted_type = pre_type + "Any" + post_type
    else:
        raise TypeError(f"Unknown type: {schema.type}")

    return TypeConversion(
        original_type=original_type,
        converted_type=converted_type,
        import_types=import_types,
    )


def _generate_property_from_schema(
        model_name: str, name: str, schema: Schema, parent_schema: Optional[Schema] = None
) -> Property:
    """
    Generates a property from a schema. It takes the type of the schema and converts it to a python type, and then
    creates the according property.
    :param model_name: Name of the model this property belongs to
    :param name: Name of the schema
    :param schema: schema to be converted
    :param parent_schema: Component this belongs to
    :return: Property
    """
    required = (
            parent_schema is not None
            and parent_schema.required is not None
            and name in parent_schema.required
    )

    import_type = None
    if required:
        import_type = [] if name == model_name else [name]

    return Property(
        name=name,
        type=type_converter(schema, required, model_name),
        required=required,
        default=None if required else "None",
        import_type=import_type,
    )


def _generate_property_from_reference(
        model_name: str,
        name: str,
        reference: Reference,
        parent_schema: Optional[Schema] = None,
        force_required: bool = False,
) -> Property:
    """
    Generates a property from a reference. It takes the name of the reference as the type, and then
    returns a property type
    :param name: Name of the schema
    :param reference: reference to be converted
    :param parent_schema: Component this belongs to
    :param force_required: Force the property to be required
    :return: Property and model to be imported by the file
    """
    required = (
                       parent_schema is not None
                       and parent_schema.required is not None
                       and name in parent_schema.required
               ) or force_required
    import_model = common.normalize_symbol(reference.ref.split("/")[-1])

    if import_model == model_name:
        type_conv = TypeConversion(
            original_type=reference.ref,
            converted_type=import_model
            if required
            else 'Optional["' + import_model + '"]',
            import_types=None,
        )
    else:
        type_conv = TypeConversion(
            original_type=reference.ref,
            converted_type=import_model
            if required
            else "Optional[" + import_model + "]",
            import_types=[f"from .{import_model} import {import_model}"],
        )
    return Property(
        name=name,
        type=type_conv,
        required=required,
        default=None if required else "None",
        import_type=[import_model],
    )

def _generate_property(
    model_name: str,
    name: str,
    schema_or_reference: Schema | Reference,
    parent_schema: Optional[Schema] = None,
) -> Property:
    if isinstance(schema_or_reference, Reference):
        return _generate_property_from_reference(
            model_name, name, schema_or_reference, parent_schema
        )

    return _generate_property_from_schema(
        model_name, name, schema_or_reference, parent_schema
    )

def _collect_properties_from_schema(model_name: str, parent_schema: Schema):
    property_iterator = (
        parent_schema.properties.items()
        if parent_schema.properties is not None
        else {}
    )
    for name, schema_or_reference in property_iterator:
        conv_property = _generate_property(
            model_name, name, schema_or_reference, parent_schema
        )
        yield conv_property

def generate_models(components: Components, pydantic_version: PydanticVersion = PydanticVersion.V2) -> List[Model]:
    """
    Receives components from an OpenAPI 3.0 specification and generates the models from it.
    It does so, by iterating over the components.schemas dictionary. For each schema, it checks if
    it is a normal schema (i.e. simple type like string, integer, etc.), a reference to another schema, or
    an array of types/references. It then computes pydantic models from it using jinja2
    :param components: The components from an OpenAPI 3.0 specification.
    :param pydantic_version: The version of pydantic to use.
    :return: A list of models.
    """
    models: List[Model] = []

    if components.schemas is None:
        return models

    jinja_env = create_jinja_env()
    for schema_name, schema_or_reference in components.schemas.items():
        name = common.normalize_symbol(schema_name)
        if schema_or_reference.enum is not None:
            value_dict = schema_or_reference.dict()
            regex = re.compile(r"[\s\/=\*\+]+")
            value_dict["enum"] = [
                re.sub(regex, "_", i) if isinstance(i, str) else f"value_{i}"
                for i in value_dict["enum"]
            ]
            m = Model(
                file_name=name,
                content=jinja_env.get_template(ENUM_TEMPLATE).render(
                    name=name, **value_dict
                ),
                openapi_object=schema_or_reference,
                properties=[],
            )
            try:
                compile(m.content, "<string>", "exec")
                models.append(m)
            except SyntaxError as e:  # pragma: no cover
                click.echo(f"Error in model {name}: {e}")

            continue  # pragma: no cover

        # Enumerate properties for this model
        properties = []
        for conv_property in _collect_properties_from_schema(name, schema_or_reference):
            properties.append(conv_property)

        # Enumerate union types that compose this model (if any) from allOf, oneOf, anyOf
        parent_components = []
        components_iterator = (
            (schema_or_reference.allOf or []) + (schema_or_reference.oneOf or []) + (schema_or_reference.anyOf or [])
        )
        for parent_component in components_iterator:
            # For references, instead of importing properties, record inherited components
            if isinstance(parent_component, Reference):
                ref = parent_component.ref
                parent_name = common.normalize_symbol(ref.split("/")[-1])
                parent_components.append(ParentModel(
                    ref = ref,
                    name = parent_name,
                    import_type = f"from .{parent_name} import {parent_name}"
                ))

            # Collect inline properties
            if isinstance(parent_component, Schema):
                for conv_property in _collect_properties_from_schema(name, parent_component):
                    properties.append(conv_property)

        template_name = MODELS_TEMPLATE_PYDANTIC_V2 if pydantic_version == PydanticVersion.V2 else MODELS_TEMPLATE

        generated_content = jinja_env.get_template(template_name).render(
            schema_name=name,
            schema=schema_or_reference,
            properties=properties,
            parent_components=parent_components
        )

        try:
            compile(generated_content, "<string>", "exec")
        except SyntaxError as e:  # pragma: no cover
            click.echo(f"Error in model {name}: {e}")  # pragma: no cover

        models.append(
            Model(
                file_name=name,
                content=generated_content,
                openapi_object=schema_or_reference,
                properties=properties,
                parent_components=parent_components
            )
        )

    return models
