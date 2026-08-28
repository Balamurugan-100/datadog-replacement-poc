from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from api.models import Product


class Command(BaseCommand):
    help = "Prepare migrations and seed records across configured database instances"

    def handle(self, *args, **options):
        configured_database_aliases = list(settings.DATABASES.keys())
        self.stdout.write(f"Setting up databases: {configured_database_aliases}")

        for database_alias in configured_database_aliases:
            self.stdout.write(f"Running migrations for database '{database_alias}'...")
            try:
                call_command("migrate", database=database_alias, interactive=False)
                self.stdout.write(self.style.SUCCESS(f"Migrations complete for '{database_alias}'"))
            except Exception as migration_error:
                self.stderr.write(f"Error migrating database '{database_alias}': {migration_error}")
                continue

            existing_product_count = Product.objects.using(database_alias).count()
            if existing_product_count == 0:
                self.stdout.write(f"Seeding sample products for database '{database_alias}'...")
                Product.objects.using(database_alias).create(
                    name=f"Product from {database_alias.upper()}",
                    description=f"Sample product record in database {database_alias}",
                    price="29.99",
                    stock=100,
                )
                Product.objects.using(database_alias).create(
                    name=f"Item {database_alias.upper()}-002",
                    description=f"Secondary test item in database {database_alias}",
                    price="49.50",
                    stock=50,
                )
                self.stdout.write(self.style.SUCCESS(f"Seeded sample products in '{database_alias}'"))
            else:
                self.stdout.write(f"Database '{database_alias}' already contains {existing_product_count} products")
