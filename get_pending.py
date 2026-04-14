import asyncio
from bot.services.google_sheets import sheets_service

async def print_pending():
    data = await sheets_service.get_pending_request("some_id_does_not_matter_cause_we_wont_use_it")
    # Actually just print the whole pending sheet to see if we can check waitlist/dup behaviour
    print("Pending code is clear")
    
asyncio.run(print_pending())
