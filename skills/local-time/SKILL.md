---
name: local-time
description: Retrieve the current time in an IANA timezone when the user asks for time-sensitive calendar information.
allowed-tools: ["current_time"]
max-risk: read
triggers: ["current time", "what time", "현재 시간", "몇 시", "시간 알려", "timezone", "타임존"]
preflight: ["Identify the requested timezone or location", "Use America/Los_Angeles only when no timezone is given"]
postflight: ["Include the timezone with the returned time", "Use the tool result without guessing"]
completion: ["The requested current time and timezone are stated", "Any timezone assumption is explicit"]
failure: ["Do not estimate the current clock", "Ask for a location only when no safe default applies"]
---

Use the time tool instead of guessing the current clock time. Default to America/Los_Angeles unless the user specifies another timezone or location.
