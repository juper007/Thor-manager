"""Discover, validate, select, and render local agent skills."""
import json
import re
from dataclasses import dataclass
from pathlib import Path


RISK_ORDER={'read':0,'safe_write':1,'elevated':2,'destructive':3}
NAME_RE=re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
EXTENDED_FIELDS=('allowed-tools','max-risk','triggers','preflight','postflight','completion','failure')


@dataclass(frozen=True)
class Skill:
    name:str
    description:str
    body:str
    allowed_tools:tuple[str,...]=()
    max_risk:str='read'
    triggers:tuple[str,...]=()
    preflight:tuple[str,...]=()
    postflight:tuple[str,...]=()
    completion:tuple[str,...]=()
    failure:tuple[str,...]=()

class SkillPrompt(str):
    def __new__(cls,value,allowed_tools=None):
        item=super().__new__(cls,value); item.allowed_tools=None if allowed_tools is None else frozenset(allowed_tools); return item


def _frontmatter(text,path):
    if not text.startswith('---\n'): raise ValueError(f'{path}: missing frontmatter')
    try: header,body=text[4:].split('\n---',1)
    except ValueError as exc: raise ValueError(f'{path}: unterminated frontmatter') from exc
    values={}
    for number,line in enumerate(header.splitlines(),2):
        if not line.strip(): continue
        if ':' not in line: raise ValueError(f'{path}:{number}: invalid metadata')
        key,value=line.split(':',1); key=key.strip(); value=value.strip()
        if key in values: raise ValueError(f'{path}:{number}: duplicate {key}')
        if value.startswith('['):
            try: value=json.loads(value)
            except json.JSONDecodeError as exc: raise ValueError(f'{path}:{number}: invalid JSON list') from exc
        values[key]=value
    return values,body.lstrip('\r\n').strip()


def parse_skill(path,known_tools=None):
    path=Path(path)
    if path.is_symlink() or not path.is_file(): raise ValueError(f'{path}: skill must be a regular file')
    if path.stat().st_size>64_000: raise ValueError(f'{path}: skill is too large')
    metadata,body=_frontmatter(path.read_text(encoding='utf-8'),path)
    name=metadata.get('name',''); description=metadata.get('description','')
    if not NAME_RE.fullmatch(name) or name!=path.parent.name: raise ValueError(f'{path}: invalid or mismatched skill name')
    if not description or not body: raise ValueError(f'{path}: description and instructions are required')
    missing=[field for field in EXTENDED_FIELDS if field not in metadata]
    if missing: raise ValueError(f'{path}: missing required fields: {", ".join(missing)}')
    def items(field):
        value=metadata.get(field,[])
        if not isinstance(value,list) or not value or not all(isinstance(item,str) and item.strip() for item in value):
            raise ValueError(f'{path}: {field} must be a non-empty JSON string list')
        return tuple(item.strip() for item in value)
    allowed=items('allowed-tools'); risk=metadata['max-risk']
    if not isinstance(risk,str) or risk not in RISK_ORDER: raise ValueError(f'{path}: invalid max-risk')
    if known_tools is not None:
        unknown=sorted(set(allowed)-set(known_tools))
        if unknown: raise ValueError(f'{path}: unknown allowed tools: {", ".join(unknown)}')
        if hasattr(known_tools,'items'):
            excessive=sorted(tool for tool in allowed if RISK_ORDER[known_tools[tool]]>RISK_ORDER[risk])
            if excessive: raise ValueError(f'{path}: tools exceed max-risk: {", ".join(excessive)}')
    fields={field:items(field) for field in ('triggers','preflight','postflight','completion','failure')}
    return Skill(name,description,body,allowed,risk,**fields)


class SkillCatalog:
    def __init__(self,skills,known_tools=None):
        self.skills=tuple(skills)
        self.read_tools=frozenset(name for name,risk in known_tools.items() if risk=='read') if hasattr(known_tools,'items') else frozenset()
        names=[skill.name for skill in self.skills]
        if len(names)!=len(set(names)): raise ValueError('duplicate skill name')

    @classmethod
    def discover(cls,root,known_tools=None):
        directory=Path(root).resolve()/'skills'
        if not directory.is_dir(): return cls((),known_tools)
        skills=[]
        for path in sorted(directory.glob('*/SKILL.md')):
            try: path.resolve().relative_to(directory)
            except ValueError as exc: raise ValueError(f'{path}: skill escapes root') from exc
            skills.append(parse_skill(path,known_tools))
        return cls(skills,known_tools)

    @staticmethod
    def _matches(trigger,text):
        trigger=trigger.casefold()
        if trigger.isascii(): return re.search(r'(?<![a-z0-9])'+re.escape(trigger)+r'(?![a-z0-9])',text) is not None
        return trigger in text

    def select(self,request):
        if isinstance(request,list):
            text=next((item.get('content','') for item in reversed(request) if item.get('role')=='user'),'')
        else: text=str(request or '')
        normalized=text.casefold()
        return tuple(skill for skill in self.skills if any(self._matches(trigger,normalized) for trigger in (skill.name,*skill.triggers)))

    def render(self,request=None):
        selected=self.select(request) if request else self.skills
        if not selected: return SkillPrompt('No task-specific skill selected. Read-only tools remain available.',self.read_tools)
        parts=[]
        for skill in selected:
            policy=''
            policy=(f'\nAllowed tools: {", ".join(skill.allowed_tools)}\nMaximum risk: {skill.max_risk}'
                f'\nPreflight: {"; ".join(skill.preflight)}\nPostflight: {"; ".join(skill.postflight)}'
                f'\nCompletion: {"; ".join(skill.completion)}\nFailure: {"; ".join(skill.failure)}')
            parts.append(f'[{skill.name}]\n{skill.description}{policy}\n{skill.body}')
        allowed={tool for skill in selected for tool in skill.allowed_tools}
        return SkillPrompt('\n\n'.join(parts),allowed)
