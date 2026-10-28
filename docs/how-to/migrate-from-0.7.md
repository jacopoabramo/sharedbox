---
icon: lucide/arrow-up-circle
---

# How to migrate from 0.7

Code written for `sharedbox` 0.7 needs one change before it runs on the next
release: a [`Shape`][sharedbox.Shape] now takes its dimensions as one tuple.
This guide shows the old and the new form.

## Pass the dimensions of a `Shape` as a tuple

`Shape` used to take each dimension as its own argument. It now takes a
single tuple, so a one-dimensional shape needs the trailing comma that makes
a tuple of one element:

```python
# 0.7
image: Annotated[numpy.ndarray, Shape(480, 640), DType("uint8")]
samples: Annotated[numpy.ndarray, Shape(1024), DType("float32")]

# 0.8
image: Annotated[numpy.ndarray, Shape((480, 640)), DType("uint8")]
samples: Annotated[numpy.ndarray, Shape((1024,)), DType("float32")]
```

The old form fails as the class is defined: `Shape(480, 640)` raises
`TypeError` because it passes two arguments, and `Shape(1024)` raises
`ValueError` because `1024` is not a tuple. Code that builds the dimensions
at run time passes the tuple it already has, `Shape(dims)`, in place of
`Shape(*dims)`.
