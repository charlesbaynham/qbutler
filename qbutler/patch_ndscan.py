from typing import Any
from typing import Tuple

from ndscan.experiment import Fragment
from ndscan.experiment.parameters import ParamStore


def reset_param(self: Fragment, param_name: str) -> Tuple[Any, ParamStore]:
    """Reset the parameter with the given name to its default value

    Undoes the work of  :meth:`override_param`, which overrides the parameter to
    a given value.

    Note that the default value are not recalculated: use
    :meth:`recompute_param_defaults` for that.

    :param param_name: The name of the parameter.

    :return: A tuple ``(param, store)`` of the parameter metadata and the
        original and now rebound :class:`.ParamStore` instance that the
        parameter handles are now bound to.
    """
    assert (
        self._free_params.get(param_name, None) is None
    ), "Already a free parameter: '{}'".format(param_name)

    fqn = self.fqn + "." + param_name

    param: Any = None
    store: ParamStore = None
    for this_param, this_store in self._default_params:
        if this_param.fqn == fqn:
            param = this_param
            store = this_store

    if param is None:
        raise KeyError("Parameter {} not found".format(param_name))

    for handle in self._get_all_handles_for_param(param_name):
        handle.set_store(store)

    self._free_params[param_name] = param

    return param, store


setattr(Fragment, "reset_param", reset_param)
