from pycyfic import optimize
import cython


@optimize
def comst(counter: cython.int) -> cython.int:
    name: cython.int = 300 + counter
    print(name)
    return name

@optimize
def plus(a: cython.int, b: cython.int) -> cython.int:
    value: cython.int = a + b
    return value

fast()

print(comst(5))
print(plus(2, 3))
