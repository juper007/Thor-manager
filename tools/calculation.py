import ast
import math
import operator
from datetime import datetime
from zoneinfo import ZoneInfo


OPS={ast.Add:operator.add,ast.Sub:operator.sub,ast.Mult:operator.mul,ast.Div:operator.truediv,ast.FloorDiv:operator.floordiv,ast.Mod:operator.mod,ast.Pow:operator.pow,ast.USub:operator.neg,ast.UAdd:operator.pos}
CONSTS={'pi':math.pi,'e':math.e,'tau':math.tau}


def _eval(node):
    if isinstance(node,ast.Expression): return _eval(node.body)
    if isinstance(node,ast.Constant) and isinstance(node.value,(int,float)): return node.value
    if isinstance(node,ast.Name) and node.id in CONSTS: return CONSTS[node.id]
    if isinstance(node,ast.UnaryOp) and type(node.op) in OPS: return OPS[type(node.op)](_eval(node.operand))
    if isinstance(node,ast.BinOp) and type(node.op) in OPS:
        left,right=_eval(node.left),_eval(node.right)
        if isinstance(node.op,ast.Pow) and abs(right)>100: raise ValueError('exponent is too large')
        return OPS[type(node.op)](left,right)
    raise ValueError('unsupported expression')


def calculator(args):
    expression=str(args.get('expression','')).strip()[:200]
    return {'expression':expression,'result':_eval(ast.parse(expression,mode='eval'))}


def current_time(args):
    zone=str(args.get('timezone','America/Los_Angeles'))
    try: now=datetime.now(ZoneInfo(zone))
    except Exception: zone='America/Los_Angeles'; now=datetime.now(ZoneInfo(zone))
    return {'timezone':zone,'iso':now.isoformat(),'formatted':now.strftime('%Y-%m-%d %H:%M:%S %Z')}
