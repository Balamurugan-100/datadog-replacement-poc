"""Integration manager for discovery, registration, and activation of library integrations."""

import importlib
import logging
from typing import Any, Dict, List, Optional, Type, Union

from tp_obs_v3.config import SDKConfig
from tp_obs_v3.integrations.base import BaseIntegration

logger = logging.getLogger("tp_obs_v3.integrations")

# Default mapping of integration names to their module and class paths
_BUILTIN_INTEGRATIONS: Dict[str, str] = {
    "django": "tp_obs_v3.integrations.django.DjangoIntegration",
}


class IntegrationManager:
    """Manages discovery, lifecycle, and activation of framework integrations."""

    def __init__(self) -> None:
        self._registered_classes: Dict[str, Union[Type[BaseIntegration], str]] = dict(
            _BUILTIN_INTEGRATIONS
        )
        self._active_instances: Dict[str, BaseIntegration] = {}

    def register(
        self,
        name: str,
        integration: Union[Type[BaseIntegration], str],
    ) -> None:
        """Register a new or custom integration class."""
        self._registered_classes[name.lower()] = integration

    def get(self, name: str) -> Optional[BaseIntegration]:
        """Get an active integration instance by name."""
        return self._active_instances.get(name.lower())

    def _resolve_class(self, entry: Union[Type[BaseIntegration], str]) -> Optional[Type[BaseIntegration]]:
        """Resolve a class reference from string or return class directly."""
        if isinstance(entry, str):
            try:
                mod_name, _, cls_name = entry.rpartition(".")
                mod = importlib.import_module(mod_name)
                return getattr(mod, cls_name)
            except (ImportError, AttributeError) as exc:
                logger.debug("Could not import integration class %s: %s", entry, exc)
                return None
        return entry

    def apply_integrations(
        self,
        config: Optional[SDKConfig] = None,
        **kwargs: Any,
    ) -> List[str]:
        """
        Apply all enabled integrations based on environment, configuration, and kwargs.
        
        Args:
            config: Active SDKConfig
            **kwargs: Direct overrides for integrations (e.g. django=False, redis=True)
            
        Returns:
            List of successfully instrumented integration names.
        """
        instrumented_names: List[str] = []
        integrations_config = config.integrations if config else {}

        for name, entry in list(self._registered_classes.items()):
            # 1. Check if explicitly disabled via kwargs or config
            if name in kwargs:
                is_enabled = bool(kwargs[name])
            elif name in integrations_config:
                is_enabled = bool(integrations_config[name])
            else:
                is_enabled = True

            if not is_enabled:
                logger.debug("Integration '%s' is disabled by configuration.", name)
                continue

            # 2. Instantiate integration if not already active
            if name not in self._active_instances:
                cls = self._resolve_class(entry)
                if cls is None:
                    continue
                self._active_instances[name] = cls()

            instance = self._active_instances[name]

            # 3. Check if target library is installed and instrument
            if instance.is_installed():
                success = instance.instrument()
                if success:
                    instrumented_names.append(name)
            else:
                logger.debug("Library for integration '%s' is not installed, skipping.", name)

        return instrumented_names

    def uninstrument_all(self) -> None:
        """Uninstrument all active integrations and restore original behaviors."""
        for name, instance in list(self._active_instances.items()):
            try:
                instance.uninstrument()
            except Exception as exc:
                logger.debug("Error uninstrumenting %s: %s", name, exc)
        self._active_instances.clear()


_GLOBAL_INTEGRATION_MANAGER = IntegrationManager()


def get_integration_manager() -> IntegrationManager:
    """Return the global IntegrationManager singleton."""
    global _GLOBAL_INTEGRATION_MANAGER
    return _GLOBAL_INTEGRATION_MANAGER
