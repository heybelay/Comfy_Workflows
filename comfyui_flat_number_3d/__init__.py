import os
import sys

vendor = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".python_packages")
if os.path.isdir(vendor) and vendor not in sys.path:
    sys.path.insert(0, vendor)

from .flat_number_3d import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
