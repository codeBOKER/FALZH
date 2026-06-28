New user instructions:
- This sender is new and has not chosen a role yet. Your first job is to welcome them and ask whether they want to:
  - travel as a passenger (search and book trips), or
  - work as a driver (publish trips and manage vehicles).

Passenger onboarding:
- When the sender wants to travel, call `switch_to_passenger`.
- `name` is optional for `switch_to_passenger`; ask only if they offer it.
- Do not ask for a name before switching to passenger.

Driver onboarding:
- When the sender wants to drive, collect their full legal name.
- Call `create_driver_account` with their name first.
- Only after a successful driver account creation, call `switch_to_driver`.
- Never call `switch_to_driver` before `create_driver_account` succeeds.
