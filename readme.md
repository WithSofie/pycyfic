# Pycyfic

#### ⚠️ This project is currently in the beta stage. It is not a stable release and does not meet the requirements for a stable stage.

A small experimental toolchain that lets you write normal Python functions with `cython`-style annotations, then lazily transpile and compile them into Cython-backed functions at runtime.

This project currently has two main files:

- `pycyfic.py` — runtime decorator and build/load pipeline
- `compiler.py` — source-to-source transpiler from annotated Python into `.pyx`-style Cython

The main idea is simple:

1. You write a normal Python function.
2. You decorate it with `@optimize`.
3. You use `cython.int`, `cython.longlong`, or your chosen alias in annotations.
4. On first call, the file is transpiled into a single `{filename}.pyx`.
5. That `.pyx` file is compiled and imported.
6. Future calls use the compiled function.

---

## What this project is good for

This project is most useful for functions that are:

- loop-heavy
- arithmetic-heavy
- mostly scalar numeric code
- light on Python object allocation
- not object-oriented performance code

Examples that fit well:

- numeric kernels
- counters
- simulation inner loops
- bitwise processing
- repeated integer arithmetic

Examples that fit less well:

- string-heavy code
- dictionary/list-heavy code
- exception-heavy code
- I/O-heavy code
- code that depends heavily on Python object semantics everywhere

---

## Current file layout

Typical project layout:

```text
.
├── pycyfic.py
├── compiler.py
├── shell.py
└── benchmark.py

At runtime, additional files/directories are created:

.
├── shell.py
├── shell.pyx
└── .pycyfic_build/

Important behavior:

There is one generated .pyx per source file, not one .pyx per function.

If shell.py has multiple @optimize functions, they are compiled into the same shell.pyx.

This makes naming cleaner and importing easier.

Requirements

You need:

Python 3.9+

Cython

setuptools

a working C compiler toolchain

On macOS, that usually means Xcode Command Line Tools must be available.

A typical install:

python3 -m pip install Cython setuptools

If your Python environment is externally managed, use your preferred isolated environment approach instead.

Quick start

Create a file such as shell.py:

from pycyfic import optimize
import cython

@optimize
def comst(counter: cython.int) -> int:
    name: cython.int = 300 + counter
    print(name)
    return name

print(comst(5))

Run it:

py shell.py

On first real call of comst(...), Pycyfic will:

read the function source

transpile it through compiler.py

write shell.pyx

compile the generated Cython module

load the compiled function

call the compiled function

Generated output shape

Given:

from pycyfic import optimize
import cython

@optimize
def comst(counter: cython.int) -> int:
    name: cython.int = 300 + counter
    print(name)
    return name

The generated .pyx content is conceptually:

cpdef comst(int counter):
    cdef int name = 300 + counter
    print(name)
    return name

Notes:

The function is forced to cpdef when processed by @optimize.

Local variable annotations like name: cython.int = ... become cdef int name = ....

A plain Python return annotation like -> int is not treated as a Cython C return type.

If you want a Cython return type, write -> cython.int or your alias equivalent.

For example:

@optimize
def add(a: cython.int, b: cython.int) -> cython.int:
    result: cython.int = a + b
    return result

becomes conceptually:

cpdef int add(int a, int b):
    cdef int result = a + b
    return result
Supported annotation style
Parameters

Cython-typed parameters are recognized only when they use:

cython.<type>

<alias>.<type>

Examples:

def f(x: cython.int): ...
def g(x: cy.int): ...
Local variables

Annotated local variables are converted when written like:

value: cython.int = 0
total: cython.longlong = 0
Return types

Return types are converted only when they are also written as Cython annotations:

def f() -> cython.int: ...

A plain Python return annotation stays Python-level and is not promoted to a C return type automatically:

def f() -> int: ...
Type mapping

compiler.py supports a remapping table. For example:

longlong -> long long

ulonglong -> unsigned long long

If a cython base type name is not found in the map, it is emitted unchanged.

That means:

cython.longlong becomes long long

cython.int stays int

Alias support

compiler.py has a configurable alias concept:

ALIAS = "cy"

So if the alias is configured as cy, this is recognized:

import cython as cy

@optimize
def add(a: cy.int, b: cy.int) -> cy.int:
    result: cy.int = a + b
    return result

Current behavior is based on the alias value passed through the compiler pipeline.

How @optimize behaves

@optimize does not immediately compile the function when the module is imported.

Instead, it returns a wrapper that:

registers the function into a per-file build state

waits until first actual call

compiles the entire file's optimized functions together

caches the compiled function

forwards future calls to the compiled implementation

This means the first call usually includes:

transpile time

Cython build time

extension import time

So the first call is not a fair performance comparison.

The useful speed comparison is usually the steady-state repeated-call performance after compilation is done.

Benchmarking

A benchmark file such as benchmark.py is useful to demonstrate the effect.

Recommended benchmark characteristics:

large nested loops

arithmetic-heavy

typed locals

typed parameters

minimal object churn

no classes

no I/O inside the hot loop

Run:

py benchmark.py

Or with larger loop counts:

py benchmark.py --outer 400 --inner 100000 --repeat 5

The benchmark should compare:

pure Python function

optimized function first-call time

optimized function steady-state time

The most important number is the steady-state speedup.

What files get generated

When you optimize functions in shell.py, Pycyfic writes:

shell.pyx

.pycyfic_build/ build artifacts

This is intentional.

Older behavior such as:

shell.py__comst.pyx
shell.py__plus.pyx

is no longer used.

The project now follows a cleaner per-source-file model:

shell.py -> shell.pyx
Recommended coding style

For best results with the current compiler, prefer this style:

from pycyfic import optimize
import cython

@optimize
def kernel(outer: cython.int, inner: cython.int) -> cython.longlong:
    i: cython.int = 0
    j: cython.int = 0
    acc: cython.longlong = 0

    for i in range(outer):
        for j in range(inner):
            acc += i * j

    return acc

This style is currently safer than mixing lots of late declarations or highly dynamic Python constructs.

Current limitations

This project is still experimental. Current limitations include:

1. It is not a full Python-to-Cython compiler

Only a targeted subset is transformed:

selected function signatures

selected local annotations

forced cpdef headers for optimized top-level functions

It does not attempt full semantic lowering of all Python constructs.

2. Only explicit cython.* or alias types are treated as Cython types

Plain Python types are not automatically treated as C types.

For example:

def f(x: int) -> int:
    ...

does not become a Cython int signature automatically.

3. Best results come from numeric code

If your code spends most of its time manipulating Python objects, speedups may be small.

4. First call includes compile overhead

This is expected because compilation is lazy.

5. Global/closure-heavy code may be more fragile

Pycyfic tries to inject runtime globals and closure values into the compiled module environment, but self-contained numeric functions are the safest target.

6. The generated .pyx is a build artifact

You should treat it as generated output, not your source of truth.

Development sanity checks

A quick syntax check:

python3 -m py_compile pycyfic.py compiler.py shell.py

Then run:

py shell.py

To inspect generated Cython source:

bat shell.pyx

or:

cat shell.pyx
Minimal example set
Example 1: simple typed add
from pycyfic import optimize
import cython

@optimize
def add(a: cython.int, b: cython.int) -> cython.int:
    result: cython.int = a + b
    return result

print(add(2, 3))
Example 2: typed loop accumulator
from pycyfic import optimize
import cython

@optimize
def summation(n: cython.int) -> cython.longlong:
    i: cython.int = 0
    total: cython.longlong = 0

    for i in range(n):
        total += i

    return total
Example 3: mixed Python and Cython annotations
from pycyfic import optimize
import cython

@optimize
def demo(a: cython.int, label: str) -> int:
    value: cython.int = a + 1
    print(label, value)
    return value

Here:

a becomes a Cython int

label stays a Python object parameter

return stays Python-level because it is plain -> int

Design summary

Pycyfic currently uses this model:

source function lives in normal .py

@optimize registers function by source file

one source file generates one .pyx

one compiled extension module is built for that generated .pyx

wrapper dispatches calls to compiled function after lazy build

This model is much cleaner than generating one .pyx per function, and it makes future reform easier.

Future directions

Likely areas for future improvement:

stronger whole-file compilation strategy

better import/global handling

richer type support

more robust nested-function handling

improved caching/rebuild strategy

more complete Cython syntax generation

better diagnostics when transpilation fails

License / status

This project is experimental and under active redesign.
Use it for exploration, benchmarks, and controlled numeric kernels first.
