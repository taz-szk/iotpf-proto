import uuid
from app.models.public import Tenant

def test_tenant_map_default_view_fields():
    t = Tenant(id=uuid.uuid4(), name="Acme", slug="acme",
               map_default_lat=35.68, map_default_lng=139.76, map_default_zoom=10)
    assert t.map_default_lat == 35.68
    assert t.map_default_lng == 139.76
    assert t.map_default_zoom == 10
