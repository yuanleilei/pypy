# indirection needed; otherwise the built-in module "struct" shadows
# any file struct.py that would be found in the user dirs
try:
    from __builtin_struct import *
except ImportError:
    from _struct import *
    from _struct import __doc__
