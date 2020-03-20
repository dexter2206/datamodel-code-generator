from typing import Dict

from datamodel_code_generator import snooper_to_methods
from datamodel_code_generator.parser.jsonschema import JsonSchemaParser
from prance import BaseParser


def class_from_path(path):
    return (
        "".join(
            (z.title() if not z.startswith("{") else "ById") for s in path for z in s.split("-")
        )
    )

@snooper_to_methods(max_variable_length=None)
class OpenAPIParser(JsonSchemaParser):
    def parse_raw(self) -> None:
        base_parser = BaseParser(
            spec_string=self.text, backend='openapi-spec-validator'
        )

        for obj_name, raw_obj in base_parser.specification['components'][
            'schemas'
        ].items():  # type: str, Dict
            self.parse_raw_obj(obj_name, raw_obj)

        for path, path_obj in base_parser.specification["paths"].items():
            print(path)
            for method, method_obj in path_obj.items():
                if method.lower() not in ["get", "post", "put", "patch", "delete"]:
                    continue
                print(method)
                for status_code, response in method_obj["responses"].items():
                    print(status_code)
                    if "content" not in response:
                        continue
                    content_type = response["content"].get("application/json", response["content"].get("text/plain"))
                    if not content_type:
                        continue
                    schema = content_type["schema"]
                    if "$ref" in schema:
                        continue
                    self.parse_raw_obj(class_from_path(path.split("/")) + method.title() + status_code, schema)
                if "requestBody" not in method_obj:
                    continue
                schema = method_obj["requestBody"]["content"]["application/json"]["schema"]
                if "$ref" in schema:
                    continue
                self.parse_raw_obj(class_from_path(path.split("/")) + method.title() + "Body", schema)
