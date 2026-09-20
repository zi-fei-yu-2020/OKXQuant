"""Bounded pure-data rules; Python notation is parsed, never executed as Python.
No dynamic imports, host attribute access, user callables or bytecode execution.
Unsupported syntax is rejected at save/load. Budget faults are uncatchable by rules.
"""
from __future__ import annotations
import ast
from dataclasses import dataclass
import math
import operator
import re
import time

ENGINE = "pure-data-ast-v1"
MAX_SOURCE_BYTES = 65_536
MAX_AST_NODES = 6_000
MAX_AST_DEPTH = 48
MAX_DATA_DEPTH = 32
MAX_ITEMS = 4_096
MAX_VALUES = 50_000
MAX_STRING = 8_192
MAX_INT_BITS = 256
MAX_ITERATIONS = 2_000
PLAN_KEY = "_validated_entry_plans"

class RuleError(ValueError):
    """Invalid/unsupported source, data, or output."""

class RuleBudgetExceeded(RuleError):
    """Uncatchable by rule try/except."""

class RuleDataRequired(RuleError):
    """A reserved pure-data input is needed; the rule cannot supply a loader."""

@dataclass
class Budget:
    steps: int = 50_000
    loops: int = MAX_ITERATIONS
    values: int = 300_000
    chars: int = 1_000_000
    seconds: float = 1.0

    def __post_init__(self):
        self.deadline = time.monotonic() + self.seconds

    def tick(self, amount=1):
        self.steps -= amount
        if self.steps < 0 or time.monotonic() > self.deadline:
            raise RuleBudgetExceeded("Rule expression/time budget exceeded")

    def iterate(self):
        self.tick(); self.loops -= 1
        if self.loops < 0:
            raise RuleBudgetExceeded("Rule loop budget exceeded")


def copy_data(value, budget=None):
    """Reject custom objects/subclasses/cycles BEFORE invoking data methods."""
    remaining = MAX_VALUES
    remaining_chars = 1_000_000
    active = set()
    def visit(item, depth):
        nonlocal remaining, remaining_chars
        remaining -= 1
        if remaining < 0 or depth > MAX_DATA_DEPTH:
            raise RuleBudgetExceeded("Rule data size/depth budget exceeded")
        if budget is not None:
            budget.values -= 1
            if budget.values < 0:
                raise RuleBudgetExceeded("Rule allocation budget exceeded")
        kind = type(item)
        if item is None or kind is bool: return item
        if kind is int:
            if item.bit_length() > MAX_INT_BITS:
                raise RuleBudgetExceeded("Rule integer budget exceeded")
            return item
        if kind is float:
            # NaN/Inf remain data: existing finite-value rules must inspect them.
            return item
        if kind is str:
            remaining_chars -= len(item)
            if remaining_chars < 0:
                raise RuleBudgetExceeded("Rule data text budget exceeded")
            if len(item) > MAX_STRING:
                raise RuleBudgetExceeded("Rule string budget exceeded")
            if budget is not None:
                budget.chars -= len(item)
                if budget.chars < 0:
                    raise RuleBudgetExceeded("Rule text allocation budget exceeded")
            return item
        if kind not in (list, tuple, dict, set, frozenset):
            raise RuleError("Rules accept plain data only, not custom objects/callables")
        if len(item) > MAX_ITEMS:
            raise RuleBudgetExceeded("Rule collection budget exceeded")
        identity = id(item)
        if identity in active: raise RuleError("Cyclic rule data is not allowed")
        active.add(identity)
        try:
            if kind is dict:
                result = {}
                for key, child in item.items():
                    if type(key) not in (str, int, float, bool, type(None)):
                        raise RuleError("Only scalar dictionary keys are supported")
                    result[visit(key, depth+1)] = visit(child, depth+1)
                return result
            children = [visit(child, depth+1) for child in item]
            return {list: list, tuple: tuple, set: set, frozenset: frozenset}[kind](children)
        finally:
            active.remove(identity)
    return visit(value, 0)


_BUILTINS = {"str", "float", "int", "bool", "abs", "min", "max", "round", "len", "isinstance", "sum", "any", "all", "range", "list", "tuple", "set", "dict", "sorted"}
_TYPES = {"str": str, "float": float, "int": int, "bool": bool, "dict": dict, "list": list, "tuple": tuple, "set": set}
_ERRORS = {name: value for name, value in (("Exception", Exception), ("ValueError", ValueError), ("TypeError", TypeError), ("ZeroDivisionError", ZeroDivisionError), ("OverflowError", OverflowError), ("KeyError", KeyError), ("IndexError", IndexError), ("ArithmeticError", ArithmeticError))}
_DATA_ERRORS = (ValueError, TypeError, ZeroDivisionError, OverflowError, KeyError, IndexError)
_MATH = {name: function for name, function in (("isfinite", math.isfinite), ("isnan", math.isnan), ("isinf", math.isinf), ("sqrt", math.sqrt), ("fabs", math.fabs), ("floor", math.floor), ("ceil", math.ceil), ("trunc", math.trunc), ("log", math.log), ("log10", math.log10), ("log2", math.log2), ("exp", math.exp), ("sin", math.sin), ("cos", math.cos), ("tan", math.tan), ("atan", math.atan), ("atan2", math.atan2), ("hypot", math.hypot), ("copysign", math.copysign))}
_MATH_CONSTANTS = {"pi": math.pi, "e": math.e, "tau": math.tau, "inf": math.inf, "nan": math.nan}
_METHODS = {"get", "keys", "values", "items", "upper", "lower", "strip", "lstrip", "rstrip", "startswith", "endswith", "replace", "split", "join", "find", "count", "isdigit", "isalpha"}
_RESERVED = _BUILTINS | set(_ERRORS) | {"math", "check_risk"}
_BINOPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow}
_CMP = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge, ast.Is: operator.is_, ast.IsNot: operator.is_not, ast.In: lambda a,b: a in b, ast.NotIn: lambda a,b: a not in b}


def _error(node, message):
    raise RuleError(f"Line {getattr(node, 'lineno', 0)}: {message}")


def _tree(source):
    if type(source) is not str or len(source) > MAX_SOURCE_BYTES or len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise RuleError("Rule source is too large or not text")
    try: tree = ast.parse(source, mode="exec")
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        raise RuleError("Invalid rule syntax or parser depth") from None
    stack = [(tree, 0)]; count = 0
    while stack:
        node, depth = stack.pop(); count += 1
        if count > MAX_AST_NODES or depth > MAX_AST_DEPTH:
            raise RuleBudgetExceeded("Rule AST size/depth budget exceeded")
        stack.extend((child, depth+1) for child in ast.iter_child_nodes(node))
    return tree


# EXACT legacy ADX idiom -> a plain context read. No plugin import is performed.
_LEGACY_PLAN = ast.parse("from scripts.entry_candidates import catalog\nfrom scripts.risk_policy import load_policy\nplans=catalog(package,vars(load_policy()))['plans']").body
_LEGACY_DUMPS = [ast.dump(n, include_attributes=False) for n in _LEGACY_PLAN]


def _migrate_plan_reads(tree):
    for node in list(ast.walk(tree)):
        for field, value in ast.iter_fields(node):
            if not isinstance(value, list) or not value or not all(isinstance(x, ast.stmt) for x in value): continue
            out=[]; i=0
            while i<len(value):
                if [ast.dump(n, include_attributes=False) for n in value[i:i+3]] == _LEGACY_DUMPS:
                    replacement=ast.parse(f"plans = context.get('{PLAN_KEY}', [])").body[0]
                    out.append(ast.copy_location(replacement, value[i])); i+=3
                else: out.append(value[i]); i+=1
            setattr(node, field, out)
    return ast.fix_missing_locations(tree)


class _Validator:
    def __init__(self, tree):
        self.names = {"package", "decision", "context"}
        for n in ast.walk(tree):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                if "__" in n.id or n.id in _RESERVED: _error(n, "Reserved/private identifiers cannot be assigned")
                self.names.add(n.id)
        self.loops = 0

    def target(self, node):
        if isinstance(node, ast.Name):
            if node.id in _RESERVED or "__" in node.id: _error(node, "Invalid assignment target")
        elif isinstance(node, (ast.Tuple, ast.List)):
            for part in node.elts: self.target(part)
        else: _error(node, "Only local names/unpacking may be assigned; data mutation is forbidden")

    def annotation(self, node):
        if node is None: return
        if isinstance(node, ast.Name) and node.id in set(_TYPES) | {"Any"}: return
        if isinstance(node, ast.Subscript): self.annotation(node.value); self.annotation(node.slice); return
        if isinstance(node, ast.Tuple):
            for part in node.elts: self.annotation(part)
            return
        _error(node, "Annotations are inert type labels only")

    def block(self, nodes):
        for n in nodes:
            if isinstance(n, ast.Return): self.expr(n.value)
            elif isinstance(n, ast.Assign):
                for target in n.targets: self.target(target)
                self.expr(n.value)
            elif isinstance(n, ast.AnnAssign): self.target(n.target); self.annotation(n.annotation); self.expr(n.value)
            elif isinstance(n, ast.AugAssign):
                self.target(n.target)
                if not isinstance(n.target, ast.Name) or type(n.op) not in _BINOPS: _error(n,"Unsupported augmented assignment")
                self.expr(n.value)
            elif isinstance(n, ast.If): self.expr(n.test); self.block(n.body); self.block(n.orelse)
            elif isinstance(n, (ast.For, ast.While)):
                if isinstance(n, ast.For): self.target(n.target); self.expr(n.iter)
                else: self.expr(n.test)
                self.loops+=1; self.block(n.body); self.loops-=1; self.block(n.orelse)
            elif isinstance(n, (ast.Break, ast.Continue)):
                if not self.loops: _error(n,"Loop control outside loop")
            elif isinstance(n, ast.Try):
                if n.finalbody: _error(n,"finally is outside the rule subset")
                self.block(n.body); self.block(n.orelse)
                for handler in n.handlers:
                    if handler.name: _error(handler,"Exception objects cannot be bound")
                    types=handler.type.elts if isinstance(handler.type,ast.Tuple) else [handler.type]
                    for t in types:
                        if t is not None and (not isinstance(t,ast.Name) or t.id not in _ERRORS): _error(handler,"Unsupported exception type")
                    self.block(handler.body)
            elif isinstance(n, ast.Import):
                if len(n.names)!=1 or n.names[0].name!="math" or n.names[0].asname: _error(n,"Only inert 'import math' is supported")
            elif isinstance(n, ast.Expr): self.expr(n.value)
            elif isinstance(n, ast.Pass): pass
            else: _error(n,"Statement is outside the pure-data rule subset")

    def comprehension(self, n):
        if len(n.generators)!=1 or n.generators[0].is_async: _error(n,"Only a single bounded comprehension is supported")
        g=n.generators[0]; self.target(g.target); self.expr(g.iter)
        for predicate in g.ifs: self.expr(predicate)
        if isinstance(n,ast.DictComp): self.expr(n.key); self.expr(n.value)
        else: self.expr(n.elt)

    def expr(self, n):
        if n is None: return
        if isinstance(n,ast.Constant): copy_data(n.value); return
        if isinstance(n,ast.Name):
            if n.id not in self.names or "__" in n.id: _error(n,"Unknown/private name; callable values are not exposed")
        elif isinstance(n,(ast.List,ast.Tuple,ast.Set)):
            if len(n.elts)>MAX_ITEMS: _error(n,"Collection too large")
            for part in n.elts: self.expr(part)
        elif isinstance(n,ast.Dict):
            if len(n.keys)>MAX_ITEMS or None in n.keys: _error(n,"Dictionary unpacking/oversized dictionaries are unsupported")
            for part in n.keys+n.values: self.expr(part)
        elif isinstance(n,ast.BinOp):
            if type(n.op) not in _BINOPS: _error(n,"Unsupported arithmetic")
            self.expr(n.left); self.expr(n.right)
        elif isinstance(n,ast.UnaryOp):
            if not isinstance(n.op,(ast.Not,ast.UAdd,ast.USub)): _error(n,"Unsupported unary operation")
            self.expr(n.operand)
        elif isinstance(n,ast.BoolOp):
            for part in n.values: self.expr(part)
        elif isinstance(n,ast.Compare):
            if any(type(op) not in _CMP for op in n.ops): _error(n,"Unsupported comparison")
            for part in [n.left]+n.comparators: self.expr(part)
        elif isinstance(n,ast.IfExp): self.expr(n.test); self.expr(n.body); self.expr(n.orelse)
        elif isinstance(n,ast.Subscript): self.expr(n.value); self.expr(n.slice)
        elif isinstance(n,ast.Slice): self.expr(n.lower); self.expr(n.upper); self.expr(n.step)
        elif isinstance(n,ast.Attribute):
            if not (isinstance(n.value,ast.Name) and n.value.id=="math" and n.attr in _MATH_CONSTANTS): _error(n,"Object attributes/private access are forbidden")
        elif isinstance(n,ast.Call):
            if n.keywords: _error(n,"Keyword calls/unpacking are outside the subset")
            if isinstance(n.func,ast.Name):
                if n.func.id not in _BUILTINS: _error(n,"Calling user/external functions is forbidden")
                if n.func.id=="isinstance":
                    if len(n.args)!=2: _error(n,"isinstance takes two arguments")
                    self.expr(n.args[0]); types=n.args[1].elts if isinstance(n.args[1],ast.Tuple) else [n.args[1]]
                    if not types or any(not isinstance(t,ast.Name) or t.id not in _TYPES for t in types): _error(n,"isinstance uses fixed data types only")
                    return
            elif isinstance(n.func,ast.Attribute):
                if isinstance(n.func.value,ast.Name) and n.func.value.id=="math":
                    if n.func.attr not in _MATH: _error(n,"Unsupported math operation")
                else:
                    if n.func.attr not in _METHODS: _error(n,"Method is not a permitted data operation")
                    self.expr(n.func.value)
            else: _error(n,"Indirect calls are forbidden")
            if len(n.args)>16: _error(n,"Too many call arguments")
            for arg in n.args:
                if isinstance(arg,ast.GeneratorExp):
                    if not isinstance(n.func,ast.Name) or n.func.id not in {"any","all","sum","min","max"} or len(n.args)!=1: _error(arg,"Generators are only bounded aggregate arguments")
                    self.comprehension(arg)
                else: self.expr(arg)
        elif isinstance(n,(ast.ListComp,ast.SetComp,ast.DictComp)): self.comprehension(n)
        elif isinstance(n,ast.JoinedStr):
            for part in n.values: self.expr(part)
        elif isinstance(n,ast.FormattedValue):
            if n.conversion not in (-1,115,114): _error(n,"Unsupported f-string conversion")
            self.expr(n.value); self.expr(n.format_spec)
        else: _error(n,"Expression is outside the pure-data rule subset")

class _Return(BaseException):
    def __init__(self,value): self.value=value
class _Break(BaseException): pass
class _Continue(BaseException): pass


def _weight(value):
    kind=type(value)
    if kind is str: return 1+len(value)//16
    if kind is dict: return 1+sum(_weight(k)+_weight(v) for k,v in value.items())
    if kind in (list,tuple,set,frozenset): return 1+sum(_weight(v) for v in value)
    return 1


class _Runner:
    def __init__(self,budget): self.b=budget;self.context=None
    def require_data(self,value,key):
        if value is self.context and type(key) is str and key==PLAN_KEY and key not in value:
            raise RuleDataRequired("Verified entry plans are required as data")
    def checked(self,value): return copy_data(value,self.b)
    def seq(self,value):
        if type(value) not in (list,tuple,set,frozenset,dict,str): raise TypeError("Expected a plain iterable")
        return value
    def bind(self,target,value,env):
        if isinstance(target,ast.Name): env[target.id]=value
        else:
            parts=list(self.seq(value))
            if len(parts)!=len(target.elts): raise ValueError("Unpacking size mismatch")
            for part,child in zip(target.elts,parts): self.bind(part,child,env)
    def binary(self,op,left,right):
        self.b.tick(_weight(left)+_weight(right))
        kind=type(op);numeric=(int,float,bool)
        if kind is ast.Mult and (type(left) in (str,list,tuple) or type(right) in (str,list,tuple)):
            value,count=(left,right) if type(left) in (str,list,tuple) else (right,left)
            if type(count) not in (int,bool): raise TypeError("Sequence repeat requires integer")
            limit=MAX_STRING if type(value) is str else MAX_ITEMS
            if len(value)*max(0,count)>limit: raise RuleBudgetExceeded("Sequence repeat budget exceeded")
        elif kind is ast.Add and type(left) in (str,list,tuple):
            if type(right) is not type(left): raise TypeError("Incompatible sequence addition")
            if len(left)+len(right)>(MAX_STRING if type(left) is str else MAX_ITEMS): raise RuleBudgetExceeded("Sequence addition budget exceeded")
        elif type(left) not in numeric or type(right) not in numeric:
            raise TypeError("Only numeric arithmetic is supported")
        if kind is ast.Pow:
            if not math.isfinite(right) or abs(right)>1024: raise RuleBudgetExceeded("Exponent budget exceeded")
            if type(left) in (int,bool) and type(right) in (int,bool) and right>0 and max(1,abs(left).bit_length())*right>MAX_INT_BITS:
                raise RuleBudgetExceeded("Integer power budget exceeded")
        result=_BINOPS[kind](left,right)
        if type(result) is complex: raise ValueError("Complex numbers are not supported")
        return self.checked(result)
    def string(self,value,representation=False):
        if type(value) is str and not representation: return value
        def size(v):
            if type(v) is str: return 4+6*len(v)
            if type(v) is dict: return 2+sum(size(k)+size(x)+4 for k,x in v.items())
            if type(v) in (list,tuple,set,frozenset): return 12+sum(size(x)+2 for x in v)
            return 82 if type(v) is int else 32
        if size(value)>MAX_STRING: raise RuleBudgetExceeded("String conversion budget exceeded")
        return self.checked(repr(value) if representation else str(value))
    def call(self,node,env):
        name=node.func.id if isinstance(node.func,ast.Name) else node.func.attr
        if isinstance(node.func,ast.Name) and name=="isinstance":
            value=self.expr(node.args[0],env)
            types=node.args[1].elts if isinstance(node.args[1],ast.Tuple) else [node.args[1]]
            return isinstance(value,tuple(_TYPES[t.id] for t in types))
        if len(node.args)==1 and isinstance(node.args[0],ast.GeneratorExp):
            return self.aggregate(name,self.comprehension(node.args[0],env))
        args=[self.expr(n,env) for n in node.args]
        self.b.tick(sum(_weight(v) for v in args))
        if isinstance(node.func,ast.Attribute):
            if isinstance(node.func.value,ast.Name) and node.func.value.id=="math":
                if any(type(v) not in (int,float,bool) for v in args): raise TypeError("Math accepts numbers only")
                return self.checked(_MATH[name](*args))
            value=self.expr(node.func.value,env);self.b.tick(_weight(value))
            if type(value) is dict:
                if name=="get":
                    if args: self.require_data(value,args[0])
                    return self.checked(dict.get(value,*args))
                if name in {"keys","values","items"} and not args:
                    return self.checked(list({"keys":dict.keys,"values":dict.values,"items":dict.items}[name](value)))
                raise TypeError("Unsupported dictionary method")
            if type(value) is not str: raise TypeError("Only explicit dictionary/string methods are supported")
            if name=="replace" and len(args) in (2,3) and type(args[0]) is str and type(args[1]) is str:
                count=value.count(args[0]) if len(args)==2 else min(value.count(args[0]),args[2]) if args[2]>=0 else value.count(args[0])
                if len(value)+max(0,count)*max(0,len(args[1])-len(args[0]))>MAX_STRING: raise RuleBudgetExceeded("String replacement budget exceeded")
            if name=="join":
                if len(args)!=1: raise TypeError("join expects one argument")
                parts=self.seq(args[0])
                if any(type(x) is not str for x in parts): raise TypeError("join requires strings")
                if sum(len(x) for x in parts)+max(0,len(parts)-1)*len(value)>MAX_STRING: raise RuleBudgetExceeded("String join budget exceeded")
            methods={"upper":str.upper,"lower":str.lower,"strip":str.strip,"lstrip":str.lstrip,"rstrip":str.rstrip,
                     "startswith":str.startswith,"endswith":str.endswith,"replace":str.replace,"split":str.split,"join":str.join,
                     "find":str.find,"count":str.count,"isdigit":str.isdigit,"isalpha":str.isalpha}
            if name not in methods: raise TypeError("Unsupported string method")
            return self.checked(methods[name](value,*args))
        if name in {"any","all","sum"}:
            if len(args)!=1: raise TypeError("Aggregate expects one argument")
            return self.aggregate(name,iter(self.seq(args[0])))
        if name in {"min","max"}:
            if not args: raise TypeError("min/max require an argument")
            values=iter(self.seq(args[0])) if len(args)==1 else iter(args)
            return self.aggregate(name,values)
        if name=="range":
            if not 1<=len(args)<=3 or any(type(x) is not int for x in args): raise TypeError("range needs integer bounds")
            sequence=range(*args)
            try: length=len(sequence)
            except OverflowError: raise RuleBudgetExceeded("Range budget exceeded") from None
            if length>MAX_ITERATIONS: raise RuleBudgetExceeded("Range budget exceeded")
            return self.checked(list(sequence))
        if name=="str":
            if len(args)>1: raise TypeError("str accepts one data value")
            return self.string(args[0]) if args else ""
        if name in {"list","tuple","set","dict","sorted"}:
            if len(args)>1: raise TypeError("Collection conversion accepts one argument")
            sequence=self.seq(args[0]) if args else []
            if name=="sorted": self.b.tick(max(1,len(sequence))*max(1,len(sequence).bit_length()))
            return self.checked({"list":list,"tuple":tuple,"set":set,"dict":dict,"sorted":sorted}[name](sequence))
        functions={"float":float,"int":int,"bool":bool,"abs":abs,"round":round,"len":len}
        if name=="int" and args and type(args[0]) is str and len(args[0])>256:
            raise RuleBudgetExceeded("Integer text conversion budget exceeded")
        if name=="round" and len(args)==2 and (type(args[1]) is not int or abs(args[1])>32): raise RuleBudgetExceeded("Round precision budget exceeded")
        return self.checked(functions[name](*args))
    def aggregate(self,name,values):
        result=0 if name=="sum" else None;seen=False
        for value in values:
            self.b.iterate()
            if name=="any" and value: return True
            if name=="all" and not value: return False
            if name=="sum": result=self.binary(ast.Add(),result,value)
            elif name in {"min","max"}:
                self.b.tick(_weight(value)+_weight(result))
                if not seen or (value<result if name=="min" else value>result): result=value
            seen=True
        if name=="any": return False
        if name=="all": return True
        if name in {"min","max"} and not seen: raise ValueError("Empty aggregate")
        return result
    def comprehension(self,node,env):
        local=dict(env);g=node.generators[0]
        for item in self.seq(self.expr(g.iter,env)):
            self.b.iterate();self.bind(g.target,item,local)
            if all(self.expr(p,local) for p in g.ifs):
                if isinstance(node,ast.DictComp): yield (self.expr(node.key,local),self.expr(node.value,local))
                else: yield self.expr(node.elt,local)
    def expr(self,n,env):
        self.b.tick()
        if n is None: return None
        if isinstance(n,ast.Constant): return n.value
        if isinstance(n,ast.Name):
            if n.id not in env: raise RuleError("Rule local variable used before assignment")
            return env[n.id]
        if isinstance(n,(ast.List,ast.Tuple,ast.Set)):
            values=[self.expr(x,env) for x in n.elts]
            return self.checked({ast.List:list,ast.Tuple:tuple,ast.Set:set}[type(n)](values))
        if isinstance(n,ast.Dict): return self.checked({self.expr(k,env):self.expr(v,env) for k,v in zip(n.keys,n.values)})
        if isinstance(n,ast.BinOp): return self.binary(n.op,self.expr(n.left,env),self.expr(n.right,env))
        if isinstance(n,ast.UnaryOp):
            value=self.expr(n.operand,env)
            if isinstance(n.op,ast.Not): return not value
            if type(value) not in (int,float,bool): raise TypeError("Unary arithmetic needs a number")
            return self.checked(-value if isinstance(n.op,ast.USub) else +value)
        if isinstance(n,ast.BoolOp):
            value=None
            for part in n.values:
                value=self.expr(part,env)
                if (isinstance(n.op,ast.And) and not value) or (isinstance(n.op,ast.Or) and value): break
            return value
        if isinstance(n,ast.Compare):
            left=self.expr(n.left,env)
            for op,next_node in zip(n.ops,n.comparators):
                right=self.expr(next_node,env);self.b.tick(_weight(left)+_weight(right))
                if not _CMP[type(op)](left,right): return False
                left=right
            return True
        if isinstance(n,ast.IfExp): return self.expr(n.body if self.expr(n.test,env) else n.orelse,env)
        if isinstance(n,ast.Slice): return slice(self.expr(n.lower,env),self.expr(n.upper,env),self.expr(n.step,env))
        if isinstance(n,ast.Subscript):
            value=self.expr(n.value,env);index=self.expr(n.slice,env)
            if type(value) not in (dict,list,tuple,str): raise TypeError("Subscript requires plain data")
            self.require_data(value,index)
            return self.checked(value[index])
        if isinstance(n,ast.Attribute): return _MATH_CONSTANTS[n.attr]
        if isinstance(n,ast.Call): return self.call(n,env)
        if isinstance(n,(ast.ListComp,ast.SetComp,ast.DictComp)):
            values=list(self.comprehension(n,env))
            return self.checked({ast.ListComp:list,ast.SetComp:set,ast.DictComp:dict}[type(n)](values))
        if isinstance(n,ast.JoinedStr):
            parts=[self.expr(x,env) for x in n.values]
            if sum(len(x) for x in parts)>MAX_STRING: raise RuleBudgetExceeded("F-string budget exceeded")
            return self.checked("".join(parts))
        if isinstance(n,ast.FormattedValue):
            value=self.expr(n.value,env)
            if n.conversion in (115,114): value=self.string(value,n.conversion==114)
            if type(value) not in (str,int,float,bool,type(None)): raise TypeError("F-string values must be scalars")
            spec=self.expr(n.format_spec,env) if n.format_spec else ""
            fields=re.fullmatch(r"[+ -]?(\d{1,3})?(?:\.(\d{1,2}))?[eEfFgG%]?",spec)
            if fields is None: raise RuleError("Unsupported format specification")
            if int(fields[1] or 0)>256 or int(fields[2] or 0)>32: raise RuleBudgetExceeded("Format width/precision budget exceeded")
            return self.checked(format(value,spec))
        raise RuleError("Unrecognized expression")
    def block(self,nodes,env):
        for n in nodes:
            self.b.tick()
            if isinstance(n,ast.Return): raise _Return(self.expr(n.value,env))
            if isinstance(n,(ast.Assign,ast.AnnAssign)):
                value=self.expr(n.value,env)
                for target in n.targets if isinstance(n,ast.Assign) else [n.target]: self.bind(target,value,env)
            elif isinstance(n,ast.AugAssign): self.bind(n.target,self.binary(n.op,self.expr(n.target,env),self.expr(n.value,env)),env)
            elif isinstance(n,ast.If): self.block(n.body if self.expr(n.test,env) else n.orelse,env)
            elif isinstance(n,(ast.For,ast.While)):
                completed=True
                iterator=iter(self.seq(self.expr(n.iter,env))) if isinstance(n,ast.For) else None
                while True:
                    if iterator is None:
                        if not self.expr(n.test,env): break
                    else:
                        try: item=next(iterator)
                        except StopIteration: break
                        self.bind(n.target,item,env)
                    self.b.iterate()
                    try: self.block(n.body,env)
                    except _Continue: continue
                    except _Break: completed=False;break
                if completed: self.block(n.orelse,env)
            elif isinstance(n,ast.Break): raise _Break()
            elif isinstance(n,ast.Continue): raise _Continue()
            elif isinstance(n,ast.Try):
                try: self.block(n.body,env)
                except RuleError: raise
                except _DATA_ERRORS as exc:
                    for handler in n.handlers:
                        names=handler.type.elts if isinstance(handler.type,ast.Tuple) else [handler.type]
                        types=tuple(_ERRORS[t.id] for t in names if t is not None)
                        if handler.type is None or isinstance(exc,types): self.block(handler.body,env);break
                    else: raise
                else: self.block(n.orelse,env)
            elif isinstance(n,ast.Expr): self.expr(n.value,env)
            elif isinstance(n,(ast.Pass,ast.Import)): pass
            else: raise RuleError("Unrecognized statement")


class RuleProgram:
    def __init__(self,tree,source):
        self._tree=tree
        self.source=source
        self.requires_plans=any(isinstance(n,ast.Constant) and n.value==PLAN_KEY for n in ast.walk(tree))
        self.docstring=ast.get_docstring(tree) or ""

    def execute(self,package,decision,context,budget=None):
        budget=budget or Budget();runner=_Runner(budget)
        values=copy_data([package,decision,context],budget)
        if any(type(v) is not dict for v in values): raise RuleError("Rule arguments must be plain dictionaries")
        env=dict(zip(("package","decision","context"),values))
        runner.context=env["context"]
        function=None
        try:
            for n in self._tree.body:
                if isinstance(n,ast.FunctionDef): function=n
                else: runner.block([n],env)
            runner.block(function.body,env)
        except _Return as ret:
            value=copy_data(ret.value,budget)
            if type(value) is not tuple or len(value)!=2 or type(value[0]) is not bool or type(value[1]) is not str:
                raise RuleError("check_risk must return (bool, str)")
            return value
        except _DATA_ERRORS as exc:
            if isinstance(exc,RuleError): raise
            raise RuleError("Rule data operation failed: "+type(exc).__name__) from None
        raise RuleError("check_risk did not return (bool, str)")


def validate_rule(source, *, migrate_legacy=False):
    tree=_tree(source)
    if migrate_legacy: tree=_migrate_plan_reads(tree)
    validator=_Validator(tree);functions=[]
    for node in tree.body:
        if isinstance(node,ast.FunctionDef):
            functions.append(node);args=node.args
            if (node.name!="check_risk" or node.decorator_list or getattr(node,"type_params",[]) or args.posonlyargs or args.vararg or args.kwarg
                    or args.kwonlyargs or args.defaults or args.kw_defaults
                    or [a.arg for a in args.args]!=["package","decision","context"]):
                _error(node,"Define only check_risk(package, decision, context), without decorators/defaults")
            for arg in args.args: validator.annotation(arg.annotation)
            validator.annotation(node.returns);validator.block(node.body)
        elif isinstance(node,(ast.Import,ast.Assign,ast.AnnAssign)) or (isinstance(node,ast.Expr) and isinstance(node.value,ast.Constant) and type(node.value.value) is str):
            validator.block([node])
        else: _error(node,"Module body may contain only constants, inert math declaration and check_risk")
    if len(functions)!=1: raise RuleError("Exactly one check_risk function is required")
    normalized=ast.unparse(tree)+"\n" if migrate_legacy and ast.dump(tree,include_attributes=False)!=ast.dump(_tree(source),include_attributes=False) else source
    if normalized != source:
        _tree(normalized)  # Migration output must obey the same persisted limits.
    return RuleProgram(tree,normalized)
