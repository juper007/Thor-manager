#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

import agent_tools


result=agent_tools.web_search({'query':'NVIDIA Jetson Thor','max_results':2})
if not result['results']:
    raise SystemExit('web search returned no results')
print(f"web_search=OK results={len(result['results'])}")
