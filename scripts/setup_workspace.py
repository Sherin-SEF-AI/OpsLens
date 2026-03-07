#!/usr/bin/env python3
"""Initialize Notion workspace with OpsLens databases."""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.notion_mcp.workspace_setup import main

if __name__ == "__main__":
    asyncio.run(main())
