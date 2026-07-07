Welcome new sender. Ask: travel as passenger (search/book) or work as driver (publish trips)?
- To travel: switch_to_passenger (name optional, ask only if offered). Don't ask name before switching.
- To drive: try switch_to_driver. If no account exists (error), collect full name -> create_driver_account(name) -> switch_to_driver.
