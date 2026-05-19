import logging
from dataclasses import dataclass

from homeassistant.helpers.entity import Entity

from ..common.entity_controller import EntityController
from ..common.entity_controller import RemoteControlMode
from ..common.types import Inv
from ..common.types import RegisterType
from .entity_factory import ENTITY_DESCRIPTION_KWARGS
from .modbus_select import ModbusSelect
from .modbus_select import ModbusSelectDescription

_LOGGER = logging.getLogger(__package__)

_FORCE_CHARGE = "Force Charge"
_FORCE_DISCHARGE = "Force Discharge"
_INVALID = "Invalid"


@dataclass(kw_only=True, **ENTITY_DESCRIPTION_KWARGS)
class ModbusWorkModeSelectDescription(ModbusSelectDescription):  # type: ignore[misc]
    def create_entity_if_supported(
        self,
        controller: EntityController,
        inverter_model: Inv,
        register_type: RegisterType,
    ) -> Entity | None:
        address = self._address_for_inverter_model(self.address, inverter_model, register_type)
        return ModbusWorkModeSelect(controller, self, address) if address is not None else None


class ModbusWorkModeSelect(ModbusSelect):
    def __init__(
        self,
        controller: EntityController,
        entity_description: ModbusSelectDescription,
        address: int,
    ) -> None:
        super().__init__(controller, entity_description, address)

        self._prev_remote_control_mode: RemoteControlMode | None = None

        if controller.remote_control_manager is not None:
            self._attr_options.extend([_FORCE_CHARGE, _FORCE_DISCHARGE])

    @property
    def current_option(self) -> str | None:
        if self._controller.remote_control_manager is not None:
            mode = self._controller.remote_control_manager.mode
            remote_control_enabled = self._controller.remote_control_manager.remote_control_enabled
            active_power = self._controller.remote_control_manager.active_power
            self._prev_remote_control_mode = mode

            _LOGGER.debug(
                "Work mode select '%s' evaluating with manager mode=%s remote_control_enabled=%s active_power=%s",
                self.entity_id,
                mode,
                remote_control_enabled,
                active_power,
            )

            if remote_control_enabled is False and mode == RemoteControlMode.DISABLE:
                self._prev_remote_control_mode = None
                selected = super().current_option
                _LOGGER.debug(
                    "Work mode select '%s' using inverter work mode option %s because remote control is disabled",
                    self.entity_id,
                    selected,
                )
                return selected
            if remote_control_enabled is True:
                if active_power is not None:
                    if active_power < 0:
                        return _FORCE_CHARGE
                    if active_power > 0:
                        return _FORCE_DISCHARGE

                if mode == RemoteControlMode.FORCE_CHARGE:
                    return _FORCE_CHARGE
                if mode == RemoteControlMode.FORCE_DISCHARGE:
                    return _FORCE_DISCHARGE

            _LOGGER.debug(
                "Work mode select '%s' returning internal '%s' state because manager mode=%s remote_control_enabled=%s and active_power=%s do not align; available options=%s. Home Assistant may show unknown if current_option is not in options.",
                self.entity_id,
                _INVALID,
                mode,
                remote_control_enabled,
                active_power,
                self._attr_options,
            )
            return _INVALID

        self._prev_remote_control_mode = None
        return super().current_option

    async def async_select_option(self, option: str) -> None:
        if option == _INVALID:
            return

        if option in (_FORCE_CHARGE, _FORCE_DISCHARGE):
            assert self._controller.remote_control_manager is not None
            mode = RemoteControlMode.FORCE_CHARGE if option == _FORCE_CHARGE else RemoteControlMode.FORCE_DISCHARGE
            await self._controller.remote_control_manager.set_mode(mode)
        else:
            if self._controller.remote_control_manager is not None:
                await self._controller.remote_control_manager.set_mode(RemoteControlMode.DISABLE)
            await super().async_select_option(option)

        # This update might not cause a register update (which is what triggers HA to update its state), so do this
        # explicitly
        self.async_schedule_update_ha_state()

    @property
    def addresses(self) -> list[int]:
        addresses = list(super().addresses)

        if self._controller.remote_control_manager is not None:
            remote_enable_address = self._controller.remote_control_manager.remote_enable_address
            if remote_enable_address is not None:
                addresses.append(remote_enable_address)
            addresses.extend(self._controller.remote_control_manager.active_power_addresses)

        return addresses

    def update_callback(self, changed_addresses: set[int]) -> None:
        super().update_callback(changed_addresses)

        # If the remote control mode has changed under us, update
        if (
            self._controller.remote_control_manager is not None
            and self._controller.remote_control_manager.mode != self._prev_remote_control_mode
        ):
            self.schedule_update_ha_state()
