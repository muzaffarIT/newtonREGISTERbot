import asyncio
import os
import sys

# add project root to sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from bot.services.google_sheets import sheets_service

async def main():
    print("Updating stats...")
    await sheets_service.update_branch_statistics_sheets()
    print("Done")

if __name__ == "__main__":
    asyncio.run(main())
