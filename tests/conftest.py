import os
import sys
from datetime import timedelta

from hypothesis import HealthCheck, settings

sys.path.insert(0, os.path.dirname(__file__))

# Every property runs a brute-force oracle, so a failure must be reported quickly rather
# than shrunk for an unbounded time: cap examples and per-example time. Hypothesis also
# stops shrinking after its own fixed budget.
settings.register_profile(
    "default",
    max_examples=300,
    deadline=timedelta(seconds=5),
    suppress_health_check=[HealthCheck.too_slow],
)
settings.register_profile(
    "ci",
    max_examples=1500,
    deadline=timedelta(seconds=5),
    suppress_health_check=[HealthCheck.too_slow],
    print_blob=True,
)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))
