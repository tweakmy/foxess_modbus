"""Read-only enum sensor backed by a Modbus register."""

import logging
from dataclasses import dataclass
from typing import Any
from typing import cast

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.sensor import SensorEntityDescription
from homeassistant.const import Platform
from homeassistant.helpers.entity import Entity

from ..common.entity_controller import EntityController
from ..common.types import Inv
from ..common.types import RegisterType
from .entity_factory import ENTITY_DESCRIPTION_KWARGS
from .entity_factory import EntityFactory
from .inverter_model_spec import InverterModelSpec
from .modbus_entity_mixin import ModbusEntityMixin

_LOGGER = logging.getLogger(__package__)


@dataclass(kw_only=True, **ENTITY_DESCRIPTION_KWARGS)
class ModbusEnumSensorDescription(SensorEntityDescription, EntityFactory):  # type: ignore[misc]
    """Description for a mapped Modbus enum sensor."""

    address: list[InverterModelSpec]
    options_map: dict[int, str]

    @property
    def entity_type(self) -> type[Entity]:
        return SensorEntity

    def create_entity_if_supported(
        self,
        controller: EntityController,
        inverter_model: Inv,
        register_type: RegisterType,
    ) -> Entity | None:
        address = self._address_for_inverter_model(self.address, inverter_model, register_type)
        return ModbusEnumSensor(controller, self, address) if address is not None else None

    def serialize(self, inverter_model: Inv, register_type: RegisterType) -> dict[str, Any] | None:
        addresses = self._addresses_for_inverter_model(self.address, inverter_model, register_type)
        if addresses is None:
            return None

        return {
            "type": "enum-sensor",
            "key": self.key,
            "name": self.name,
            "addresses": addresses,
            "values": self.options_map,
        }


class ModbusEnumSensor(ModbusEntityMixin, SensorEntity):
    """Read-only enum sensor class."""

    def __init__(
        self,
        controller: EntityController,
        entity_description: ModbusEnumSensorDescription,
        address: int,
    ) -> None:
        self._attr_device_class = SensorDeviceClass.ENUM
        self._attr_options = list(entity_description.options_map.values())

        self._controller = controller
        self.entity_description = entity_description
        self._address = address
        self.entity_id = self._get_entity_id(Platform.SENSOR)

    @property
    def native_value(self) -> str | None:
        entity_description = cast(ModbusEnumSensorDescription, self.entity_description)
        value = self._controller.read(self._address, signed=False)
        if value is None:
            return None

        selected = entity_description.options_map.get(value)
        if selected is None:
            _LOGGER.warning(
                "Enum sensor value (%s) for address (%s) is not valid. Valid values: (%s)",
                value,
                self._address,
                entity_description.options_map,
            )

        return selected

    @property
    def addresses(self) -> list[int]:
        return [self._address]