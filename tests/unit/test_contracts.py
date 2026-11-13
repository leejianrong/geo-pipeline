import pytest
from pydantic import ValidationError

from geopipe.geo import pixel_to_map
from geopipe.jobs import derive_job_id
from geopipe.messages import Job, Options


def test_unknown_schema_version_rejected():
    with pytest.raises(ValidationError):
        Job(schema_version=2, tenant_id="t", job_id="j", bucket="b", key="k")


def test_tenant_id_required():
    with pytest.raises(ValidationError):
        Job(job_id="j", bucket="b", key="k")


def test_job_id_is_deterministic_and_option_sensitive():
    a = derive_job_id("t", "b", "k", "etag", Options())
    assert a == derive_job_id("t", "b", "k", "etag", Options())
    assert a != derive_job_id("t", "b", "k", "etag", Options(overlap=0))
    assert a != derive_job_id("t", "b", "k", "other-etag", Options())


def test_geotransform_north_up_and_rotated():
    assert pixel_to_map((100, 2, 0, 500, 0, -2), 10, 5) == (120, 490)
    assert pixel_to_map((0, 1, 0.5, 0, 0.5, -1), 2, 4) == (4, -3)
