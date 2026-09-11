from pathlib import Path
from typing import assert_type

from madako import DataProfile, ProfilePlan, ProfileResult


def check_public_api(storage_dir: Path) -> None:
    profile = assert_type(DataProfile.from_storage(storage_dir), DataProfile)
    plan = assert_type(profile.plan(), ProfilePlan)
    result = assert_type(profile.run(plan), ProfileResult)

    assert_type(profile.profile(), ProfileResult)
    assert_type(result.successful, bool)
    assert_type(result.models_path, Path)
