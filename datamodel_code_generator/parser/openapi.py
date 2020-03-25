from typing import Dict

from datamodel_code_generator import snooper_to_methods
from datamodel_code_generator.parser.jsonschema import JsonSchemaParser
from prance import BaseParser


def class_from_path(path):
    return "".join(
        (z.title() if not z.startswith("{") else "ById") for s in path for z in s.split("-")
    )


@snooper_to_methods(max_variable_length=None)
class OpenAPIParser(JsonSchemaParser):
    def parse_raw(self) -> None:
        base_parser = BaseParser(spec_string=self.text, backend="openapi-spec-validator")

        for obj_name, raw_obj in base_parser.specification["components"][
            "schemas"
        ].items():  # type: str, Dict
            self.parse_raw_obj(obj_name, raw_obj)

        # query parameters
        for obj_name, raw_obj in base_parser.specification["components"].get("parameters", {}).items():
            if raw_obj.get("in", "") == "query" and "schema" in raw_obj:
                if "$ref" in raw_obj["schema"]:
                    continue
                self.parse_raw_obj(obj_name[0].upper() + obj_name[1:] + "QueryParam", raw_obj["schema"])

        for path, path_obj in base_parser.specification["paths"].items():
            for method, method_obj in path_obj.items():
                if method.lower() not in ["get", "post", "put", "patch", "delete"]:
                    continue

                for status_code, response in method_obj["responses"].items():
                    if "content" not in response:
                        continue
                    content_type = response["content"].get(
                        "application/json", response["content"].get("text/plain")
                    )
                    if not content_type:
                        continue
                    schema = content_type["schema"]
                    if "$ref" in schema:
                        continue
                    self.parse_raw_obj(
                        class_from_path(path.split("/")) + method.title() + status_code, schema
                    )

                if "requestBody" not in method_obj or "$ref" in method_obj["requestBody"]:
                    continue
                model_name = class_from_path(path.split("/")) + method.title() + "Body"
                if "schema" in method_obj["requestBody"]:
                    self.parse_raw_obj(model_name, method_obj["requestBody"]["schema"])
                    continue
                content = method_obj["requestBody"].get("content", None)
                if not content:
                    print(path, method, "Unable to parse " + str(method_obj["requestBody"]))
                    continue
                if "$ref" in content:
                    continue
                if "schema" in content:
                    self.parse_raw_obj(model_name, content["schema"])
                    continue
                content_type = content.get("application/json", None)
                if not content_type:
                    print(path, method, "Unable to parse " + str(method_obj["requestBody"]))
                    continue
                if "$ref" in content_type:
                    continue
                if "schema" in content_type:
                    self.parse_raw_obj(model_name, content_type["schema"])
