import json
from typing import (
    Any,
    Callable,
    DefaultDict,
    Dict,
    List,
    Optional,
    Set,
    Tuple,
    Type,
    Union,
)

from datamodel_code_generator import PythonVersion, snooper_to_methods
from datamodel_code_generator.imports import Import
from datamodel_code_generator.model import DataModel, DataModelField
from datamodel_code_generator.model.enum import Enum
from pydantic import BaseModel, Field

from ..parser.base import Parser, get_singular_name
from ..types import DataType, Types


def get_model_by_path(schema: Dict[str, Any], keys: List[str]) -> Dict:
    if len(keys) == 1:
        return schema[keys[0]]
    return get_model_by_path(schema[keys[0]], keys[1:])


json_schema_data_formats: Dict[str, Dict[str, Types]] = {
    'integer': {'int32': Types.int32, 'int64': Types.int64, 'default': Types.integer},
    'number': {
        'float': Types.float,
        'double': Types.double,
        'time': Types.time,
        'default': Types.number,
    },
    'string': {
        'default': Types.string,
        'byte': Types.byte,  # base64 encoded string
        'binary': Types.binary,
        'date': Types.date,
        'date-time': Types.date_time,
        'password': Types.password,
        'email': Types.email,
        'uuid': Types.uuid,
        'uuid1': Types.uuid1,
        'uuid2': Types.uuid2,
        'uuid3': Types.uuid3,
        'uuid4': Types.uuid4,
        'uuid5': Types.uuid5,
        'uri': Types.uri,
        'ipv4': Types.ipv4,
        'ipv6': Types.ipv6,
        'url': Types.url
    },
    'boolean': {'default': Types.boolean},
    'object': {'default': Types.object},
}


class JsonSchemaObject(BaseModel):
    items: Union[List['JsonSchemaObject'], 'JsonSchemaObject', None]
    uniqueItem: Optional[bool]
    type: Optional[str] = "object"
    format: Optional[str]
    pattern: Optional[str]
    minLength: Optional[int]
    maxLength: Optional[int]
    minimum: Optional[float]
    maximum: Optional[float]
    multipleOf: Optional[float]
    exclusiveMaximum: Optional[bool]
    exclusiveMinimum: Optional[bool]
    additionalProperties: Union['JsonSchemaObject', bool, None]
    anyOf: List['JsonSchemaObject'] = []
    allOf: List['JsonSchemaObject'] = []
    enum: List[str] = []
    writeOnly: Optional[bool]
    properties: Optional[Dict[str, 'JsonSchemaObject']]
    required: List[str] = []
    ref: Optional[str] = Field(default=None, alias='$ref')  # type: ignore
    nullable: Optional[bool] = False
    x_enum_varnames: List[str] = Field(  # type: ignore
        default=[], alias='x-enum-varnames'
    )
    description: Optional[str]

    @property
    def is_object(self) -> bool:
        return self.properties is not None or self.type == 'object'

    @property
    def is_array(self) -> bool:
        return self.items is not None or self.type == 'array'

    @property
    def ref_object_name(self) -> str:
        return self.ref.rsplit('/', 1)[-1]  # type: ignore


JsonSchemaObject.update_forward_refs()


@snooper_to_methods(max_variable_length=None)
class JsonSchemaParser(Parser):
    def __init__(
        self,
        data_model_type: Type[DataModel],
        data_model_root_type: Type[DataModel],
        data_model_field_type: Type[DataModelField] = DataModelField,
        base_class: Optional[str] = None,
        custom_template_dir: Optional[str] = None,
        extra_template_data: Optional[DefaultDict[str, Dict]] = None,
        target_python_version: PythonVersion = PythonVersion.PY_37,
        text: Optional[str] = None,
        result: Optional[List[DataModel]] = None,
        dump_resolve_reference_action: Optional[Callable[[List[str]], str]] = None,
    ):
        super().__init__(
            data_model_type,
            data_model_root_type,
            data_model_field_type,
            base_class,
            custom_template_dir,
            extra_template_data,
            target_python_version,
            text,
            result,
            dump_resolve_reference_action,
        )

    def get_data_type(self, obj: JsonSchemaObject) -> DataType:
        if obj.type is None:
            raise ValueError(f'invalid schema object {obj}')

        format_ = obj.format if obj.format in json_schema_data_formats else "default"
        return self.data_model_type.get_data_type(
            json_schema_data_formats[obj.type][format_], **obj.dict()
        )

    def get_class_name(self, field_name: str) -> str:
        if '.' in field_name:
            prefix, field_name = field_name.rsplit('.', 1)
            prefix += '.'
        else:
            prefix = ''

        class_name = super().get_class_name(field_name)

        return f'{prefix}{class_name}'

    def set_additional_properties(self, name: str, obj: JsonSchemaObject) -> None:
        if not obj.additionalProperties:
            return

        # TODO check additional property types.
        self.extra_template_data[name][
            'additionalProperties'
        ] = obj.additionalProperties

    def parse_any_of(self, name: str, obj: JsonSchemaObject) -> List[DataType]:
        any_of_data_types: List[DataType] = []
        for any_of_item in obj.anyOf:
            if any_of_item.ref:  # $ref
                any_of_data_types.append(
                    self.data_type(
                        type=any_of_item.ref_object_name,
                        ref=True,
                        version_compatible=True,
                    )
                )
            elif not any(v for k, v in vars(any_of_item).items() if k != 'type'):
                # trivial types
                any_of_data_types.append(self.get_data_type(any_of_item))
            elif (
                any_of_item.is_array
                and isinstance(any_of_item.items, JsonSchemaObject)
                and not any(
                    v for k, v in vars(any_of_item.items).items() if k != 'type'
                )
            ):
                # trivial item types
                any_of_data_types.append(
                    self.data_type(
                        type=f"List[{self.get_data_type(any_of_item.items).type_hint}]",
                        imports_=[Import(from_='typing', import_='List')],
                    )
                )
            else:
                singular_name = get_singular_name(name)
                self.parse_object(singular_name, any_of_item)
                any_of_data_types.append(
                    self.data_type(
                        type=singular_name, ref=True, version_compatible=True
                    )
                )
        return any_of_data_types

    def parse_all_of(self, name: str, obj: JsonSchemaObject) -> List[DataType]:
        fields: List[DataModelField] = []
        base_classes: List[DataType] = []
        for all_of_item in obj.allOf:
            if all_of_item.ref:  # $ref
                base_classes.append(
                    self.data_type(
                        type=all_of_item.ref_object_name,
                        ref=True,
                        version_compatible=True,
                    )
                )

            else:
                fields_ = self.parse_object_fields(all_of_item)
                fields.extend(fields_)
        self.set_additional_properties(name, obj)
        data_model_type = self.data_model_type(
            name,
            fields=fields,
            base_classes=[b.type for b in base_classes],
            auto_import=False,
            custom_base_class=self.base_class,
            custom_template_dir=self.custom_template_dir,
            extra_template_data=self.extra_template_data,
        )
        # add imports for the fields
        for f in fields:
            data_model_type.imports.extend(f.imports)
        self.append_result(data_model_type)

        return [self.data_type(type=name, ref=True, version_compatible=True)]

    def parse_object_fields(self, obj: JsonSchemaObject) -> List[DataModelField]:
        properties: Dict[str, JsonSchemaObject] = (
            obj.properties if obj.properties is not None else {}
        )
        requires: Set[str] = {*obj.required} if obj.required is not None else {*()}
        fields: List[DataModelField] = []

        for field_name, field in properties.items():  # type: ignore
            if field_name == "or":
                field_name = "or_"
            is_list = False
            field_types: List[DataType]
            if field.ref:
                field_types = [
                    self.data_type(
                        type=field.ref_object_name, ref=True, version_compatible=True
                    )
                ]
            elif field.is_array:
                class_name = self.get_class_name(field_name)
                array_fields, array_field_classes = self.parse_array_fields(
                    class_name, field
                )
                field_types = array_fields[0].data_types
                is_list = True
            elif field.anyOf:
                field_types = self.parse_any_of(field_name, field)
            elif field.allOf:
                field_types = self.parse_all_of(field_name, field)
            elif field.is_object:
                if field.properties:
                    class_name = self.get_class_name(field_name)
                    self.parse_object(class_name, field)
                    field_types = [
                        self.data_type(
                            type=class_name, ref=True, version_compatible=True
                        )
                    ]
                else:
                    field_types = [
                        self.data_type(
                            type='Dict[str, Any]',
                            imports_=[
                                Import(from_='typing', import_='Any'),
                                Import(from_='typing', import_='Dict'),
                            ],
                        )
                    ]
            elif field.enum:
                enum = self.parse_enum(field_name, field)
                field_types = [
                    self.data_type(type=enum.name, ref=True, version_compatible=True)
                ]
            else:
                data_type = self.get_data_type(field)
                field_types = [data_type]
            required: bool = field_name in requires
            fields.append(
                self.data_model_field_type(
                    name=field_name,
                    data_types=field_types,
                    required=required,
                    is_list=is_list,
                )
            )
        return fields

    def parse_object(self, name: str, obj: JsonSchemaObject) -> None:
        fields = self.parse_object_fields(obj)

        self.set_additional_properties(name, obj)

        data_model_type = self.data_model_type(
            name,
            fields=fields,
            description=obj.description,
            custom_base_class=self.base_class,
            custom_template_dir=self.custom_template_dir,
            extra_template_data=self.extra_template_data,
        )
        self.append_result(data_model_type)

    def parse_array_fields(
        self, name: str, obj: JsonSchemaObject
    ) -> Tuple[List[DataModelField], List[DataType]]:
        if isinstance(obj.items, JsonSchemaObject):
            items: List[JsonSchemaObject] = [obj.items]
        else:
            items = obj.items  # type: ignore
        item_obj_data_types: List[DataType] = []
        is_union: bool = False
        for item in items:
            if item.ref:
                item_obj_data_types.append(
                    self.data_type(
                        type=item.ref_object_name, ref=True, version_compatible=True
                    )
                )
            elif isinstance(item, JsonSchemaObject) and item.properties:
                singular_name = get_singular_name(name)
                self.parse_object(singular_name, item)
                item_obj_data_types.append(
                    self.data_type(
                        type=singular_name, ref=True, version_compatible=True
                    )
                )
            elif item.anyOf:
                item_obj_data_types.extend(self.parse_any_of(name, item))
                is_union = True
            elif item.allOf:
                singular_name = get_singular_name(name)
                item_obj_data_types.extend(self.parse_all_of(singular_name, item))
            else:
                item_obj_data_types.append(self.get_data_type(item))

        field = self.data_model_field_type(
            data_types=item_obj_data_types,
            required=True,
            is_list=True,
            is_union=is_union,
        )
        return [field], item_obj_data_types

    def parse_array(self, name: str, obj: JsonSchemaObject) -> None:
        fields, item_obj_names = self.parse_array_fields(name, obj)
        data_model_root = self.data_model_root_type(
            name,
            fields,
            custom_base_class=self.base_class,
            custom_template_dir=self.custom_template_dir,
            extra_template_data=self.extra_template_data,
        )

        self.append_result(data_model_root)

    def parse_root_type(self, name: str, obj: JsonSchemaObject) -> None:
        if obj.type:
            types: List[DataType] = [self.get_data_type(obj)]
        elif obj.anyOf:
            types = self.parse_any_of(name, obj)
        else:
            types = [
                self.data_type(
                    type=obj.ref_object_name, ref=True, version_compatible=True
                )
            ]

        data_model_root_type = self.data_model_root_type(
            name,
            [self.data_model_field_type(data_types=types, required=not obj.nullable)],
            custom_base_class=self.base_class,
            custom_template_dir=self.custom_template_dir,
            extra_template_data=self.extra_template_data,
        )
        self.append_result(data_model_root_type)

    def parse_enum(self, name: str, obj: JsonSchemaObject) -> DataModel:
        enum_fields = []

        for i, enum_part in enumerate(obj.enum):  # type: ignore
            if obj.type == 'string':
                default = f"'{enum_part}'"
                field_name = enum_part
            else:
                default = enum_part
                if obj.x_enum_varnames:
                    field_name = obj.x_enum_varnames[i]
                else:
                    field_name = f'{obj.type}_{enum_part}'
            enum_fields.append(
                self.data_model_field_type(name=field_name, default=default)
            )

        enum = Enum(self.get_class_name(name), fields=enum_fields)
        self.append_result(enum)
        return enum

    def parse_ref(self, obj: JsonSchemaObject) -> None:
        if obj.ref:
            ref: str = obj.ref
            # https://swagger.io/docs/specification/using-ref/
            if obj.ref.startswith('#'):
                # Local Reference – $ref: '#/definitions/myElement'
                pass
            elif '://' in ref:
                # URL Reference – $ref: 'http://path/to/your/resource' Uses the whole document located on the different server.
                raise NotImplementedError(f'URL Reference is not supported. $ref:{ref}')

            else:
                # Remote Reference – $ref: 'document.json' Uses the whole document located on the same server and in the same location.
                # TODO treat edge case
                relative_path, object_path = ref.split('#/')
                full_path = self.base_path / relative_path
                with full_path.open() as f:
                    if full_path.suffix.lower() == '.json':
                        import json

                        ref_body: Dict[str, Any] = json.load(f)
                    else:
                        # expect yaml
                        import yaml

                        ref_body = yaml.safe_load(f)
                    object_parents = object_path.split('/')
                    ref_path = str(full_path) + '#/' + object_path
                    if ref_path not in self.excludes_ref_path:
                        self.excludes_ref_path.add(str(full_path) + '#/' + object_path)
                        model = get_model_by_path(ref_body, object_parents)
                        self.parse_raw_obj(object_parents[-1], model)

        if obj.items:
            if isinstance(obj.items, JsonSchemaObject):
                self.parse_ref(obj.items)
            else:
                for item in obj.items:
                    self.parse_ref(item)
        if isinstance(obj.additionalProperties, JsonSchemaObject):
            self.parse_ref(obj.additionalProperties)
        for item in obj.anyOf:
            self.parse_ref(item)
        for item in obj.allOf:
            self.parse_ref(item)
        if obj.properties:
            for value in obj.properties.values():
                self.parse_ref(value)

    def parse_raw_obj(self, name: str, raw: Dict) -> None:
        obj = JsonSchemaObject.parse_obj(raw)
        if obj.allOf:
            self.parse_all_of(name, obj)
        elif obj.is_object:
            self.parse_object(name, obj)
        elif obj.is_array:
            self.parse_array(name, obj)
        elif obj.enum:
            self.parse_enum(name, obj)

        else:
            self.parse_root_type(name, obj)

        self.parse_ref(obj)

    def parse_raw(self) -> None:
        raw_obj: Dict[str, Any] = json.loads(self.text)  # type: ignore
        obj_name = raw_obj.get('title', 'Model')
        self.parse_raw_obj(obj_name, raw_obj)
