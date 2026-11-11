import argparse
import json
import re
import sys
from pathlib import Path


NAME = r"[A-Za-z_][A-Za-z0-9_]*"
UNARY = {"NOT", "BUF"}
BINARY = {"AND", "OR", "XOR", "NAND", "NOR", "XNOR"}


def parse_source(text):
    inputs, gates, outputs = {}, {}, []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        inp = re.fullmatch(rf"INPUT\s+({NAME})\s+([1-9][0-9]*)", line)
        out = re.fullmatch(rf"OUTPUT\s+({NAME})", line)
        gate = re.fullmatch(rf"({NAME})\s*=\s*([A-Z]+)\((.*?)\)", line)
        if inp:
            name, width = inp.groups()
            if name in inputs or name in gates:
                raise ValueError("duplicate signal")
            inputs[name] = int(width)
        elif out:
            name = out[1]
            if name in outputs:
                raise ValueError("duplicate output")
            outputs.append(name)
        elif gate:
            name, op, body = gate.groups()
            args = [arg.strip() for arg in body.split(",")]
            if name in inputs or name in gates or op not in UNARY | BINARY:
                raise ValueError("duplicate signal or unsupported operator")
            if len(args) != (1 if op in UNARY else 2):
                raise ValueError("wrong arity")
            if any(not re.fullmatch(rf"(?:{NAME}|[01X]+)", a) for a in args):
                raise ValueError("invalid operand")
            gates[name] = (op, args)
        else:
            raise ValueError("invalid source line")
    if not outputs:
        raise ValueError("no outputs")
    return inputs, gates, outputs


def logic(op, a, b=None):
    if op == "BUF":
        return a
    if op == "NOT":
        return {"0": "1", "1": "0", "X": "X"}[a]
    if op in {"AND", "NAND"}:
        result = "0" if "0" in (a, b) else ("1" if a == b == "1" else "X")
    elif op in {"OR", "NOR"}:
        result = "1" if "1" in (a, b) else ("0" if a == b == "0" else "X")
    else:
        result = "X" if "X" in (a, b) else ("1" if a != b else "0")
    return logic("NOT", result) if op in {"NAND", "NOR", "XNOR"} else result


def evaluate(circuit, values):
    inputs, gates, outputs = circuit
    if not isinstance(values, dict) or set(values) != set(inputs):
        raise ValueError("provide exactly the declared inputs")
    for name, width in inputs.items():
        value = values[name]
        if not isinstance(value, str) or not re.fullmatch("[01X]+", value) or len(value) != width:
            raise ValueError("invalid input value or width")
    cache, active = dict(values), set()

    def resolve(name):
        if name not in inputs and name not in gates and re.fullmatch("[01X]+", name):
            return name
        if name in cache:
            return cache[name]
        if name in active:
            raise ValueError("cycle")
        if name not in gates:
            raise ValueError("undefined signal")
        active.add(name)
        op, args = gates[name]
        operands = [resolve(arg) for arg in args]
        width = max(map(len, operands))
        if any(len(value) not in (1, width) for value in operands):
            raise ValueError("incompatible widths")
        expanded = [value * width if len(value) == 1 else value for value in operands]
        cache[name] = "".join(logic(op, *bits) for bits in zip(*expanded))
        active.remove(name)
        return cache[name]

    for name in gates:
        resolve(name)
    return {name: resolve(name) for name in outputs}


def main():
    parser = argparse.ArgumentParser(description="Evaluate a circuit")
    parser.add_argument("path")
    parser.add_argument("--inputs", required=True, help="JSON object of bitstrings")
    args = parser.parse_args()
    try:
        circuit = parse_source(Path(args.path).read_text())
        print(json.dumps(evaluate(circuit, json.loads(args.inputs)), sort_keys=True))
    except (ValueError, OSError, RecursionError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
