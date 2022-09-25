from weakref import ref

# %%

valid = True


def invalidate(ref):
    print(f"Invalidating {ref}")
    global valid
    valid = False


class BigClass:
    pass


# %%

a = BigClass()

r = ref(a, invalidate)

assert valid


assert r() == a

del a

assert not valid

assert r() is None
