def hello():
    """
    A test function to print "Hello World"
    """
    print("Hello world!")


def add_things(a, b):
    """
    Returns the sum of the passed parameters.

    Note the style of this docstring: this is a Google docstring. See
    https://sphinxcontrib-napoleon.readthedocs.io/en/latest/example_google.html
    for more information.

    Because of the sphinx integrations, I can cross-reference other objects like
    the :func:`.hello` function or this :mod:`.hello` module. Using InterSphinx,
    I can even refer to things in other projects' documentation, like the python
    docs for a :class:`list`. To set this up for packages other than the python
    standard library, see conf.py in the docs folder.

    Args:

        a (float): A number
        b (float): A number

    Returns:

        float: The sum of a and b
    """
    return a + b
