import re
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from datetime import date
from types import ModuleType
from typing import Annotated, Any, NewType, TypeAlias, cast, get_args

import pytest
from fastapi import APIRouter, Body, Depends, FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import BaseModel
from pytest_fixture_classes import fixture_class
from starlette.responses import FileResponse

from tests._data import latest
from tests._data.latest import some_schema
from tests._data.unversioned_schema_dir import UnversionedSchema2
from tests._data.unversioned_schema_dir.unversioned_schemas import UnversionedSchema1
from tests._data.unversioned_schemas import UnversionedSchema3
from tests.conftest import GenerateTestVersionPackages

# TODO: It's bad to import between tests like that
from universi import VersionBundle, VersionedAPIRouter
from universi.exceptions import RouterGenerationError
from universi.structure import Version, endpoint, schema
from universi.structure.endpoints import AlterEndpointSubInstruction
from universi.structure.enums import AlterEnumSubInstruction, enum
from universi.structure.schemas import AlterSchemaSubInstruction
from universi.structure.versions import VersionChange

Endpoint: TypeAlias = Callable[..., Awaitable[Any]]


@pytest.fixture()
def router() -> VersionedAPIRouter:
    return VersionedAPIRouter()


@pytest.fixture()
def test_path() -> str:
    return "/test/{hewoo}"


@pytest.fixture()
def test_endpoint(router: VersionedAPIRouter, test_path: str) -> Endpoint:
    @router.get(test_path)
    async def test(hewwo: int):
        raise NotImplementedError

    return test


def client(router: APIRouter) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@fixture_class(name="create_versioned_copies")
class CreateVersionedCopies:
    api_version_var: ContextVar[date | None]

    def __call__(
        self,
        router: VersionedAPIRouter,
        *instructions: AlterSchemaSubInstruction | AlterEndpointSubInstruction | AlterEnumSubInstruction,
        latest_schemas_module: ModuleType | None = None,
    ) -> dict[date, VersionedAPIRouter]:
        class MyVersionChange(VersionChange):
            description = "..."
            instructions_to_migrate_to_previous_version = instructions

        return router.create_versioned_copies(
            VersionBundle(
                Version(date(2001, 1, 1), MyVersionChange),
                Version(date(2000, 1, 1)),
                api_version_var=self.api_version_var,
            ),
            latest_schemas_module=latest_schemas_module,
        )


@fixture_class(name="create_versioned_api_routes")
class CreateVersionedAPIRoutes:
    create_versioned_copies: CreateVersionedCopies

    def __call__(
        self,
        router: VersionedAPIRouter,
        *instructions: AlterSchemaSubInstruction | AlterEndpointSubInstruction | AlterEnumSubInstruction,
        latest_schemas_module: ModuleType | None = None,
    ) -> tuple[list[APIRoute], list[APIRoute]]:
        routers = self.create_versioned_copies(
            router,
            *instructions,
            latest_schemas_module=latest_schemas_module,
        )
        for router in routers.values():
            for route in router.routes:
                assert isinstance(route, APIRoute)
        return cast(
            tuple[list[APIRoute], list[APIRoute]],
            (routers[date(2000, 1, 1)].routes, routers[date(2001, 1, 1)].routes),
        )


def test__router_generation__forgot_to_generate_schemas__error(
    router: VersionedAPIRouter,
    create_versioned_api_routes: CreateVersionedAPIRoutes,
):
    with pytest.raises(
        RouterGenerationError,
        match="Versioned schema directory '.+' does not exist.",
    ):
        create_versioned_api_routes(router, latest_schemas_module=latest)


def test__endpoint_didnt_exist(
    router: VersionedAPIRouter,
    test_endpoint: Endpoint,
    test_path: str,
    create_versioned_api_routes: CreateVersionedAPIRoutes,
):
    routes_2000, routes_2001 = create_versioned_api_routes(
        router,
        endpoint(test_path, ["GET"]).didnt_exist,
    )

    assert routes_2000 == []
    assert len(routes_2001) == 1
    assert routes_2001[0].endpoint.func == test_endpoint


# TODO: Add a test for removing an endpoint and adding it back
def test__endpoint_existed(
    router: VersionedAPIRouter,
    create_versioned_api_routes: CreateVersionedAPIRoutes,
):
    @router.only_exists_in_older_versions
    @router.get("/test")
    async def test_endpoint():
        raise NotImplementedError

    @router.post("/test")
    async def test_endpoint_post():
        raise NotImplementedError

    routes_2000, routes_2001 = create_versioned_api_routes(
        router,
        endpoint("/test", ["GET"]).existed,
    )

    assert len(routes_2000) == 2
    assert routes_2000[0].endpoint.func == test_endpoint_post
    assert routes_2000[1].endpoint.func == test_endpoint

    assert len(routes_2001) == 1
    assert routes_2001[0].endpoint.func == test_endpoint_post


@pytest.mark.parametrize(
    ("attr", "attr_value"),
    [
        ("path", "/wow"),
        ("status_code", 204),
        ("tags", ["foo", "bar"]),
        ("summary", "my summary"),
        ("description", "my description"),
        ("response_description", "my response description"),
        ("deprecated", True),
        ("include_in_schema", False),
        ("name", "my name"),
        ("openapi_extra", {"my_openapi_extra": "openapi_extra"}),
        ("responses", {405: {"description": "hewwo"}, 500: {"description": "hewwo1"}}),
        ("methods", ["GET", "POST"]),
        ("operation_id", "my_operation_id"),
        ("response_class", FileResponse),
        ("dependencies", [Depends(lambda: "hewwo")]),  # pragma: no cover
        (
            "generate_unique_id_function",
            lambda api_route: api_route.endpoint.__name__,
        ),  # pragma: no cover
    ],
)
def test__endpoint_had(
    router: VersionedAPIRouter,
    attr: str,
    attr_value: Any,
    test_endpoint: Endpoint,
    test_path: str,
    create_versioned_api_routes: CreateVersionedAPIRoutes,
):
    routes_2000, routes_2001 = create_versioned_api_routes(
        router,
        endpoint(test_path, ["GET"]).had(**{attr: attr_value}),
    )

    assert len(routes_2000) == len(routes_2001) == 1
    assert getattr(routes_2000[0], attr) == attr_value
    assert getattr(routes_2001[0], attr) != attr_value


def test__endpoint_only_exists_in_older_versions__endpoint_is_not_a_route__error(
    router: VersionedAPIRouter,
    test_endpoint: Endpoint,
):
    with pytest.raises(
        LookupError,
        match=re.escape("Route not found on endpoint: 'test2'"),
    ):

        @router.only_exists_in_older_versions
        async def test2():
            raise NotImplementedError


def test__router_generation__non_api_route_added(
    router: VersionedAPIRouter,
    test_endpoint: Endpoint,
    test_path: str,
    create_versioned_copies: CreateVersionedCopies,
):
    @router.websocket("/test2")
    async def test_websocket():
        raise NotImplementedError

    routers = create_versioned_copies(router, endpoint(test_path, ["GET"]).didnt_exist)
    assert len(routers[date(2000, 1, 1)].routes) == 1
    assert len(routers[date(2001, 1, 1)].routes) == 2
    route = routers[date(2001, 1, 1)].routes[0]
    assert isinstance(route, APIRoute)
    assert route.endpoint.func == test_endpoint


def test__router_generation__creating_a_synchronous_endpoint__error(
    router: VersionedAPIRouter,
    create_versioned_copies: CreateVersionedCopies,
):
    @router.get("/test")
    def test():
        raise NotImplementedError

    with pytest.raises(
        RouterGenerationError,
        match=re.escape("All versioned endpoints must be asynchronous."),
    ):
        create_versioned_copies(router, endpoint("/test", ["GET"]).didnt_exist)


def test__router_generation__changing_a_deleted_endpoint__error(
    router: VersionedAPIRouter,
    create_versioned_copies: CreateVersionedCopies,
):
    @router.only_exists_in_older_versions
    @router.get("/test")
    async def test():
        raise NotImplementedError

    with pytest.raises(
        RouterGenerationError,
        match=re.escape(
            'Endpoint "[\'GET\'] /test" you tried to change in "MyVersionChange" doesn\'t exist',
        ),
    ):
        create_versioned_copies(router, endpoint("/test", ["GET"]).had(description="Hewwo"))


def test__router_generation__deleting_a_deleted_endpoint__error(
    router: VersionedAPIRouter,
    create_versioned_copies: CreateVersionedCopies,
):
    @router.only_exists_in_older_versions
    @router.get("/test")
    async def test():
        raise NotImplementedError

    with pytest.raises(
        RouterGenerationError,
        match=re.escape(
            'Endpoint "[\'GET\'] /test" you tried to delete in "MyVersionChange" doesn\'t exist in a newer version',
        ),
    ):
        create_versioned_copies(router, endpoint("/test", ["GET"]).didnt_exist)


def test__router_generation__re_creating_an_existing_endpoint__error(
    router: VersionedAPIRouter,
    test_endpoint: Endpoint,
    test_path: str,
    create_versioned_copies: CreateVersionedCopies,
):
    with pytest.raises(
        RouterGenerationError,
        match=re.escape(
            'Endpoint "[\'GET\'] /test/{hewoo}" you tried to re-create in "MyVersionChange" already existed in a newer version',
        ),
    ):
        create_versioned_copies(router, endpoint(test_path, ["GET"]).existed)


def test__router_generation__editing_an_endpoint_with_wrong_method__should_raise_error(
    router: VersionedAPIRouter,
    test_endpoint: Endpoint,
    test_path: str,
    create_versioned_copies: CreateVersionedCopies,
):
    with pytest.raises(
        RouterGenerationError,
        match=re.escape('Endpoint "[\'POST\'] /test/{hewoo}" you tried to change in "MyVersionChange" doesn\'t exist'),
    ):
        create_versioned_copies(router, endpoint(test_path, ["POST"]).had(description="Hewwo"))


def test__router_generation__editing_an_endpoint_with_a_less_general_method__should_raise_error(
    router: VersionedAPIRouter,
    create_versioned_copies: CreateVersionedCopies,
):
    @router.route("/test/{hewoo}", methods=["GET", "POST"])
    async def test(hewwo: int):
        raise NotImplementedError

    with pytest.raises(
        RouterGenerationError,
        match=re.escape('Endpoint "[\'GET\'] /test/{hewoo}" you tried to change in "MyVersionChange" doesn\'t exist'),
    ):
        create_versioned_copies(router, endpoint("/test/{hewoo}", ["GET"]).had(description="Hewwo"))


def test__router_generation__editing_an_endpoint_with_a_more_general_method__should_raise_error(
    router: VersionedAPIRouter,
    test_endpoint: Endpoint,
    test_path: str,
    create_versioned_copies: CreateVersionedCopies,
):
    with pytest.raises(
        RouterGenerationError,
        match=re.escape('Endpoint "[\'POST\'] /test/{hewoo}" you tried to change in "MyVersionChange" doesn\'t exist'),
    ):
        create_versioned_copies(router, endpoint(test_path, ["GET", "POST"]).had(description="Hewwo"))


def test__router_generation__editing_multiple_methods_of_multiple_endpoints__should_edit_both_methods(
    router: VersionedAPIRouter,
    create_versioned_api_routes: CreateVersionedAPIRoutes,
):
    @router.get("/test")
    async def test_get():
        raise NotImplementedError

    @router.post("/test")
    async def test_post():
        raise NotImplementedError

    routes_2000, routes_2001 = create_versioned_api_routes(
        router,
        endpoint("/test", ["GET", "POST"]).had(description="Meaw"),
    )
    assert routes_2000[0].description == "Meaw"
    assert routes_2000[1].description == "Meaw"

    assert routes_2001[0].description == ""
    assert routes_2001[1].description == ""


def get_nested_field_type(annotation: Any) -> type[BaseModel]:
    return get_args(get_args(annotation)[1])[0].__fields__["foo"].type_.__fields__["foo"].annotation


def test__router_generation__re_creating_a_non_endpoint__error(
    router: VersionedAPIRouter,
    create_versioned_copies: CreateVersionedCopies,
):
    with pytest.raises(
        RouterGenerationError,
        match=re.escape(
            'Endpoint "[\'GET\'] /test" you tried to re-create in "MyVersionChange" wasn\'t among the deleted routes',
        ),
    ):
        create_versioned_copies(router, endpoint("/test", ["GET"]).existed)


def test__router_generation__changing_attribute_to_the_same_value__error(
    router: VersionedAPIRouter,
    test_endpoint: Endpoint,
    test_path: str,
    create_versioned_copies: CreateVersionedCopies,
):
    with pytest.raises(
        RouterGenerationError,
        match=re.escape(
            'Expected attribute "path" of endpoint "[\'GET\'] /test/{hewoo}" to be different in "MyVersionChange", but it'
            " was the same. It means that your version change has no effect on the attribute and can be removed.",
        ),
    ):
        create_versioned_copies(router, endpoint(test_path, ["GET"]).had(path=test_path))


def test__router_generation__non_api_route_added_with_schemas(
    router: VersionedAPIRouter,
    test_endpoint: Endpoint,
    test_path: str,
    generate_test_version_packages: GenerateTestVersionPackages,
    create_versioned_copies: CreateVersionedCopies,
):
    @router.websocket("/test2")
    async def test_websocket():
        raise NotImplementedError

    generate_test_version_packages()
    routers = create_versioned_copies(
        router,
        endpoint(test_path, ["GET"]).didnt_exist,
        latest_schemas_module=latest,
    )
    assert len(routers[date(2000, 1, 1)].routes) == 1
    assert len(routers[date(2001, 1, 1)].routes) == 2
    route = routers[date(2001, 1, 1)].routes[0]
    assert isinstance(route, APIRoute)
    assert route.endpoint.func == test_endpoint


def test__router_generation__updating_response_model_when_schema_is_defined_in_a_non_init_file(
    router: VersionedAPIRouter,
    _reload_autogenerated_modules: None,
    generate_test_version_packages: GenerateTestVersionPackages,
    create_versioned_api_routes: CreateVersionedAPIRoutes,
):
    @router.get("/test", response_model=some_schema.MySchema)
    async def test():
        raise NotImplementedError

    instruction = schema(some_schema.MySchema).field("foo").had(type=str)
    generate_test_version_packages(instruction)

    routes_2000, routes_2001 = create_versioned_api_routes(
        router,
        instruction,
        latest_schemas_module=latest,
    )
    assert routes_2000[0].response_model.__fields__["foo"].annotation == str
    assert routes_2001[0].response_model.__fields__["foo"].annotation == int


def test__router_generation__updating_response_model(
    router: VersionedAPIRouter,
    _reload_autogenerated_modules: None,
    generate_test_version_packages: GenerateTestVersionPackages,
    create_versioned_api_routes: CreateVersionedAPIRoutes,
):
    @router.get(
        "/test",
        response_model=dict[str, list[latest.SchemaWithOnePydanticField]],
    )
    async def test():
        raise NotImplementedError

    instruction = schema(latest.SchemaWithOneIntField).field("foo").had(type=list[str])
    schemas_2000, schemas_2001 = generate_test_version_packages(instruction)

    routes_2000, routes_2001 = create_versioned_api_routes(
        router,
        instruction,
        latest_schemas_module=latest,
    )
    assert len(routes_2000) == len(routes_2001) == 1
    assert routes_2000[0].response_model == dict[str, list[schemas_2000.SchemaWithOnePydanticField]]
    assert routes_2001[0].response_model == dict[str, list[schemas_2001.SchemaWithOnePydanticField]]

    assert get_nested_field_type(routes_2000[0].response_model) == list[str]
    assert get_nested_field_type(routes_2001[0].response_model) == int


def test__router_generation__updating_request_models(
    router: VersionedAPIRouter,
    _reload_autogenerated_modules: None,
    generate_test_version_packages: GenerateTestVersionPackages,
    create_versioned_api_routes: CreateVersionedAPIRoutes,
):
    @router.get("/test")
    async def test(body: dict[str, list[latest.SchemaWithOnePydanticField]]):
        raise NotImplementedError

    instruction = schema(latest.SchemaWithOneIntField).field("foo").had(type=list[str])
    schemas_2000, schemas_2001 = generate_test_version_packages(instruction)

    routes_2000, routes_2001 = create_versioned_api_routes(
        router,
        instruction,
        latest_schemas_module=latest,
    )
    assert len(routes_2000) == len(routes_2001) == 1
    assert (
        routes_2000[0].dependant.body_params[0].annotation == dict[str, list[schemas_2000.SchemaWithOnePydanticField]]
    )
    assert (
        routes_2001[0].dependant.body_params[0].annotation == dict[str, list[schemas_2001.SchemaWithOnePydanticField]]
    )

    assert get_nested_field_type(routes_2000[0].dependant.body_params[0].annotation) == list[str]
    assert get_nested_field_type(routes_2001[0].dependant.body_params[0].annotation) == int


def test__router_generation__using_unversioned_models(
    router: VersionedAPIRouter,
    _reload_autogenerated_modules: None,
    generate_test_version_packages: GenerateTestVersionPackages,
    create_versioned_api_routes: CreateVersionedAPIRoutes,
):
    @router.get("/test")
    async def test1(body: UnversionedSchema1):
        raise NotImplementedError

    @router.get("/test2")
    async def test2(body: UnversionedSchema2):
        raise NotImplementedError

    @router.get("/test3")
    async def test3(body: UnversionedSchema3):
        raise NotImplementedError

    instruction = schema(latest.SchemaWithOneIntField).field("foo").had(type=list[str])
    generate_test_version_packages(instruction)

    routes_2000, routes_2001 = create_versioned_api_routes(
        router,
        instruction,
        latest_schemas_module=latest,
    )

    assert len(routes_2000) == len(routes_2001) == 3
    assert routes_2000[0].dependant.body_params[0].type_ is UnversionedSchema1
    assert routes_2001[0].dependant.body_params[0].type_ is UnversionedSchema1

    assert routes_2000[1].dependant.body_params[0].type_ is UnversionedSchema2
    assert routes_2001[1].dependant.body_params[0].type_ is UnversionedSchema2

    assert routes_2000[2].dependant.body_params[0].type_ is UnversionedSchema3
    assert routes_2001[2].dependant.body_params[0].type_ is UnversionedSchema3


def test__router_generation__using_weird_typehints(
    router: VersionedAPIRouter,
    _reload_autogenerated_modules: None,
    generate_test_version_packages: GenerateTestVersionPackages,
    create_versioned_api_routes: CreateVersionedAPIRoutes,
):
    newtype = NewType("newtype", str)

    @router.get("/test")
    async def test(param1: newtype = Body(), param2: str | int = Body()):
        raise NotImplementedError

    instruction = schema(latest.SchemaWithOneIntField).field("foo").had(type=list[str])
    generate_test_version_packages(instruction)

    routes_2000, routes_2001 = create_versioned_api_routes(
        router,
        instruction,
        latest_schemas_module=latest,
    )
    assert len(routes_2000) == len(routes_2001) == 1
    assert routes_2000[0].dependant.body_params[0].annotation is newtype
    assert routes_2001[0].dependant.body_params[0].annotation is newtype

    assert routes_2000[0].dependant.body_params[1].annotation == str | int
    assert routes_2001[0].dependant.body_params[1].annotation == str | int


# TODO: This test should become multiple tests
def test__router_generation__updating_request_depends(
    router: VersionedAPIRouter,
    _reload_autogenerated_modules: None,
    generate_test_version_packages: GenerateTestVersionPackages,
    api_version_var: ContextVar[date | None],
    create_versioned_copies: CreateVersionedCopies,
):
    def sub_dependency1(my_enum: latest.StrEnum) -> latest.StrEnum:
        return my_enum

    def dependency1(dep: latest.StrEnum = Depends(sub_dependency1)):  # noqa: B008
        return dep

    def sub_dependency2(my_enum: latest.StrEnum) -> latest.StrEnum:
        return my_enum

    # TODO: What if "a" gets deleted?
    def dependency2(
        dep: Annotated[latest.StrEnum, Depends(sub_dependency2)] = latest.StrEnum.a,
    ):
        return dep

    @router.get("/test1")
    async def test_with_dep1(dep: latest.StrEnum = Depends(dependency1)):  # noqa: B008
        return dep

    @router.get("/test2")
    async def test_with_dep2(dep: latest.StrEnum = Depends(dependency2)):  # noqa: B008
        return dep

    instruction = enum(latest.StrEnum).had(foo="bar")
    generate_test_version_packages(instruction)

    routers = create_versioned_copies(router, instruction, latest_schemas_module=latest)
    app_2000 = FastAPI()
    app_2001 = FastAPI()
    app_2000.include_router(routers[date(2000, 1, 1)])
    app_2001.include_router(routers[date(2001, 1, 1)])
    client_2000 = TestClient(app_2000)
    client_2001 = TestClient(app_2001)
    assert client_2000.get("/test1", params={"my_enum": "bar"}).json() == "bar"
    assert client_2000.get("/test2", params={"my_enum": "bar"}).json() == "bar"

    assert client_2001.get("/test1", params={"my_enum": "bar"}).json() == {
        "detail": [
            {
                "loc": ["query", "my_enum"],
                "msg": "value is not a valid enumeration member; permitted: '1'",
                "type": "type_error.enum",
                "ctx": {"enum_values": ["1"]},
            },
        ],
    }

    assert client_2001.get("/test2", params={"my_enum": "bar"}).json() == {
        "detail": [
            {
                "loc": ["query", "my_enum"],
                "msg": "value is not a valid enumeration member; permitted: '1'",
                "type": "type_error.enum",
                "ctx": {"enum_values": ["1"]},
            },
        ],
    }


def test__router_generation__updating_unused_dependencies(
    router: VersionedAPIRouter,
    _reload_autogenerated_modules: None,
    generate_test_version_packages: GenerateTestVersionPackages,
    create_versioned_copies: CreateVersionedCopies,
    api_version_var: ContextVar[date | None],
):
    def dependency(my_enum: latest.StrEnum):
        return my_enum

    @router.get("/test", dependencies=[Depends(dependency)])
    async def test_with_dep():
        pass

    instruction = enum(latest.StrEnum).had(foo="bar")
    generate_test_version_packages(instruction)

    routers = create_versioned_copies(router, instruction, latest_schemas_module=latest)
    client_2000 = client(routers[date(2000, 1, 1)])
    client_2001 = client(routers[date(2001, 1, 1)])
    assert client_2000.get("/test", params={"my_enum": "bar"}).json() is None

    assert client_2001.get("/test", params={"my_enum": "bar"}).json() == {
        "detail": [
            {
                "loc": ["query", "my_enum"],
                "msg": "value is not a valid enumeration member; permitted: '1'",
                "type": "type_error.enum",
                "ctx": {"enum_values": ["1"]},
            },
        ],
    }


def test__cascading_router_exists(router: VersionedAPIRouter, api_version_var: ContextVar[date | None]):
    @router.only_exists_in_older_versions
    @router.get("/test")
    async def test_with_dep1():
        return 83

    class V2002(VersionChange):
        description = ""
        instructions_to_migrate_to_previous_version = [endpoint("/test", ["GET"]).existed]

    versions = VersionBundle(
        Version(date(2002, 1, 1), V2002),
        Version(date(2001, 1, 1)),
        Version(date(2000, 1, 1)),
        api_version_var=api_version_var,
    )
    routers = router.create_versioned_copies(versions, latest_schemas_module=None)

    assert client(routers[date(2002, 1, 1)]).get("/test").json() == {
        "detail": "Not Found",
    }

    assert client(routers[date(2001, 1, 1)]).get("/test").json() == 83

    assert client(routers[date(2000, 1, 1)]).get("/test").json() == 83


def test__cascading_router_didnt_exist(router: VersionedAPIRouter, api_version_var: ContextVar[date | None]):
    @router.get("/test")
    async def test_with_dep1():
        return 83

    class V2002(VersionChange):
        description = ""
        instructions_to_migrate_to_previous_version = [
            endpoint("/test", ["GET"]).didnt_exist,
        ]

    versions = VersionBundle(
        Version(date(2002, 1, 1), V2002),
        Version(date(2001, 1, 1)),
        Version(date(2000, 1, 1)),
        api_version_var=api_version_var,
    )

    routers = router.create_versioned_copies(versions, latest_schemas_module=None)

    assert client(routers[date(2002, 1, 1)]).get("/test").json() == 83

    assert client(routers[date(2001, 1, 1)]).get("/test").json() == {
        "detail": "Not Found",
    }

    assert client(routers[date(2000, 1, 1)]).get("/test").json() == {
        "detail": "Not Found",
    }
