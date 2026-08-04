"""Intentional demo of a missing third-party import (not executed by unit tests).

CI Janitor maps ``yaml`` -> ``PyYAML`` with high confidence.
"""

# Demo only — do not import this module from package tests.
import yaml  # noqa: F401
