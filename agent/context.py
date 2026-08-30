"""Bounded conversation context with deterministic, auditable compression."""


def compact_messages(messages,max_characters=120_000,keep_recent=8):
    total=sum(len(item.get('content','')) for item in messages)
    if total<=max_characters: return list(messages),None
    keep=max(1,min(int(keep_recent),len(messages)))
    older=messages[:-keep]; recent=[]; remaining=max(2_000,max_characters//2)
    for item in reversed(messages[-keep:]):
        content=item.get('content',''); allowed=max(0,remaining)
        recent.append({**item,'content':content[-allowed:] if len(content)>allowed else content}); remaining-=min(len(content),allowed)
    recent.reverse()
    budget=max(1_000,max_characters-sum(len(item.get('content','')) for item in recent)-500)
    parts=[]; used=0
    for item in older:
        content=' '.join(item.get('content','').split())
        excerpt=content[:min(600,max(0,budget-used))]
        line=f"{item.get('role','unknown')}: {excerpt}"
        if used+len(line)>budget: break
        parts.append(line); used+=len(line)+1
    summary='Earlier conversation summary (automatically compacted):\n'+'\n'.join(parts)
    return [{'role':'system','content':summary},*recent],{'original_characters':total,'compacted_characters':sum(len(x['content']) for x in recent)+len(summary),'messages_compacted':len(older)}
