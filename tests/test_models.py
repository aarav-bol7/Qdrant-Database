import uuid

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.documents.models import Document
from apps.tenants.models import Bot, Tenant


@pytest.mark.django_db
class TestTenantModel:
    def test_create_with_valid_slug_succeeds(self):
        t = Tenant.objects.create(tenant_id="pizzapalace", name="Pizza Palace")
        t.full_clean()
        assert t.pk == "pizzapalace"

    @pytest.mark.parametrize(
        "bad_id",
        [
            "",
            "ab",
            "_pizza",
            "Pizza",
            "pizza-palace",
            "pizza palace",
            "a" * 41,
            "1!",
        ],
    )
    def test_invalid_slug_rejected_by_full_clean(self, bad_id):
        t = Tenant(tenant_id=bad_id, name="anything")
        with pytest.raises(ValidationError):
            t.full_clean()

    def test_str(self):
        t = Tenant(tenant_id="pizzapalace", name="Pizza Palace")
        assert str(t) == "pizzapalace"


@pytest.mark.django_db
class TestBotModel:
    def test_collection_name_auto_populated_on_save(self):
        t = Tenant.objects.create(tenant_id="pizzapalace", name="Pizza Palace")
        b = Bot.objects.create(tenant=t, bot_id="supportv1", name="Support")
        assert b.collection_name == "t_pizzapalace__b_supportv1"

    def test_unique_bot_per_tenant_enforced(self):
        t = Tenant.objects.create(tenant_id="pizzapalace", name="Pizza Palace")
        Bot.objects.create(tenant=t, bot_id="supportv1", name="Support")
        with transaction.atomic(), pytest.raises(IntegrityError):
            Bot.objects.create(tenant=t, bot_id="supportv1", name="Duplicate")

    def test_same_bot_id_in_different_tenants_allowed(self):
        t1 = Tenant.objects.create(tenant_id="pizzapalace", name="Pizza Palace")
        t2 = Tenant.objects.create(tenant_id="burgerbarn", name="Burger Barn")
        b1 = Bot.objects.create(tenant=t1, bot_id="supportv1", name="Support 1")
        b2 = Bot.objects.create(tenant=t2, bot_id="supportv1", name="Support 2")
        assert b1.collection_name != b2.collection_name

    def test_collection_name_unique_constraint(self):
        t1 = Tenant.objects.create(tenant_id="pizzapalace", name="Pizza Palace")
        Bot.objects.create(tenant=t1, bot_id="supportv1", name="Support 1")
        b = Bot._meta.get_field("collection_name")
        assert b.unique is True

    def test_cascade_delete_from_tenant(self):
        t = Tenant.objects.create(tenant_id="pizzapalace", name="Pizza Palace")
        Bot.objects.create(tenant=t, bot_id="supportv1", name="Support")
        Bot.objects.create(tenant=t, bot_id="ordersv1", name="Orders")
        assert Bot.objects.filter(tenant=t).count() == 2
        t.delete()
        assert Bot.objects.count() == 0

    def test_str(self):
        t = Tenant.objects.create(tenant_id="pizzapalace", name="Pizza Palace")
        b = Bot.objects.create(tenant=t, bot_id="supportv1", name="Support")
        assert str(b) == "pizzapalace/supportv1"


@pytest.mark.django_db
class TestDocumentModel:
    def _make_bot(self):
        t = Tenant.objects.create(tenant_id="pizzapalace", name="Pizza Palace")
        return Bot.objects.create(tenant=t, bot_id="supportv1", name="Support")

    def test_doc_id_is_uuid_and_auto_generated(self):
        b = self._make_bot()
        d = Document.objects.create(
            bot_ref=b,
            tenant_id=b.tenant_id,
            bot_id=b.bot_id,
            source_type="pdf",
            content_hash="sha256:abc",
        )
        assert isinstance(d.doc_id, uuid.UUID)

    def test_doc_id_can_be_supplied(self):
        b = self._make_bot()
        explicit = uuid.uuid4()
        d = Document.objects.create(
            doc_id=explicit,
            bot_ref=b,
            tenant_id=b.tenant_id,
            bot_id=b.bot_id,
            source_type="pdf",
            content_hash="sha256:abc",
        )
        assert d.doc_id == explicit

    def test_status_default_is_pending(self):
        b = self._make_bot()
        d = Document.objects.create(
            bot_ref=b,
            tenant_id=b.tenant_id,
            bot_id=b.bot_id,
            source_type="pdf",
            content_hash="sha256:abc",
        )
        assert d.status == Document.PENDING

    def test_cascade_delete_from_bot(self):
        b = self._make_bot()
        Document.objects.create(
            bot_ref=b,
            tenant_id=b.tenant_id,
            bot_id=b.bot_id,
            source_type="pdf",
            content_hash="sha256:abc",
        )
        b.delete()
        assert Document.objects.count() == 0
