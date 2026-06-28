from app.ai.tool_schemas import get_all_tool_schemas, get_tool_schemas


def test_new_user_tools_are_onboarding_only():
    names = {schema["function"]["name"] for schema in get_tool_schemas("new_user")}
    assert names == {
        "about_falsa",
        "create_driver_account",
        "switch_to_driver",
        "switch_to_passenger",
    }


def test_driver_tools_exclude_passenger_booking_tools():
    names = {schema["function"]["name"] for schema in get_tool_schemas("driver")}
    assert "search_trips" not in names
    assert "create_booking_lead" not in names
    assert "initiate_trip_action" in names
    assert "update_trip_field" in names
    assert "switch_to_passenger" in names


def test_passenger_tools_exclude_driver_management_tools():
    names = {schema["function"]["name"] for schema in get_tool_schemas("passenger")}
    assert "add_trip_by_driver" not in names
    assert "check_driver_info" not in names
    assert "switch_to_driver" in names
    assert "create_driver_account" in names


def test_all_tool_schemas_include_every_tool():
    names = {schema["function"]["name"] for schema in get_all_tool_schemas()}
    assert len(names) == 14
