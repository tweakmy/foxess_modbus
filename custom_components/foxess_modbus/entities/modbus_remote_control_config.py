from dataclasses import dataclass
from enum import Enum
from enum import auto
from typing import Callable

from homeassistant.components.number import NumberDeviceClass
from homeassistant.components.number import NumberMode
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.components.sensor import SensorStateClass

from ..common.entity_controller import EntityController
from ..common.entity_controller import EntityRemoteControlManager
from ..common.types import Inv
from ..common.types import RegisterType
from .entity_factory import EntityFactory
from .inverter_model_spec import EntitySpec
from .inverter_model_spec import InverterModelSpec
from .inverter_model_spec import ModbusAddressSpecBase
from .modbus_enum_sensor import ModbusEnumSensorDescription
from .modbus_remote_control_number import ModbusRemoteControlNumberDescription
from .modbus_remote_control_select import ModbusRemoteControlSelectDescription
from .modbus_sensor import ModbusSensorDescription


class WorkMode(Enum):
    SELF_USE = auto()
    FEED_IN_FIRST = auto()
    BACK_UP = auto()


@dataclass(frozen=True)
class ModbusRemoteControlAddressConfig:
    """Defines the set of registers used for remote control"""

    remote_enable: int
    """Remote Enable, turns remote control off/on"""
    timeout_set: int
    """Remote Timeout_Set, sets the watchdog reload value"""
    active_power: list[int]
    """Remote control-Active power command, sets the output power (+ve) or input power (-ve) of the inverter"""
    work_mode: int | None
    """Work mode control"""
    work_mode_map: dict[WorkMode, int] | None
    """Map of work mode ->value"""
    export_limit: list[int] | None
    """Export limit registers (optional)"""
    battery_soc: list[int]
    """Current battery SoC. If multiple values, these are the socs of the different batteries"""
    max_soc: int | None
    """Configured Max SoC"""
    invbatpower: list[int]
    """Current battery charge (negative) / discharge (positive) power"""
    pwr_limit_bat_up: list[int] | None
    """Prw_limit Bat_up, maximum power that the battery can accept"""
    pv_voltages: list[int]
    """Array of pvx_voltage addresses for PV strings"""


class RemoteControlAddressSpec:
    """
    Specifies the addresses involved in remote control, for a given list of inverter models

    For example:
    addresses=[
        RemoteControlAddressSpec([H1, AC1], input=ModbusRemoteControlAddressConfig(...))
        RemoteControlAddressSpec([H3], holding=ModbusRemoteControlAddressConfig(period_start_address=...))
    ]
    """

    def __init__(
        self,
        models: Inv,
        input: ModbusRemoteControlAddressConfig | None = None,  # noqa: A002
        holding: ModbusRemoteControlAddressConfig | None = None,
    ) -> None:
        self.models = models
        self.register_types: dict[RegisterType, ModbusRemoteControlAddressConfig] = {}
        if input is not None:
            self.register_types[RegisterType.INPUT] = input
        if holding is not None:
            self.register_types[RegisterType.HOLDING] = holding

    def get_all_models(self) -> EntitySpec:
        return EntitySpec(register_types=list(self.register_types.keys()), models=self.models)

    def get_models_without_work_mode(self) -> EntitySpec:
        """Gets a InverterModelSpec instance to describe the Work Mode address"""
        return EntitySpec(
            register_types=[k for k, v in self.register_types.items() if v.work_mode is None], models=self.models
        )

    def get_models_without_max_soc(self) -> EntitySpec:
        """Gets a InverterModelSpec instance to describe the Max SoC address"""
        return EntitySpec(
            register_types=[k for k, v in self.register_types.items() if v.max_soc is None], models=self.models
        )

    def _get_address(self, accessor: Callable[[ModbusRemoteControlAddressConfig], int | None]) -> InverterModelSpec:
        addresses = {}
        for register_type, address_config in self.register_types.items():
            address = accessor(address_config)
            addresses[register_type] = [address] if address is not None else None
        return ModbusAddressSpecBase(addresses, self.models)

    def _get_addresses(
        self, accessor: Callable[[ModbusRemoteControlAddressConfig], list[int] | None]
    ) -> InverterModelSpec:
        addresses = {}
        for register_type, address_config in self.register_types.items():
            addresses[register_type] = accessor(address_config)
        return ModbusAddressSpecBase(addresses, self.models)

    def get_remote_enable_address(self) -> InverterModelSpec:
        """Gets an InverterModelSpec instance to describe the remote enable address."""
        return self._get_address(lambda config: config.remote_enable)

    def get_work_mode_address(self) -> InverterModelSpec:
        """Gets an InverterModelSpec instance to describe the work mode address."""
        return self._get_address(lambda config: config.work_mode)

    def get_active_power_addresses(self) -> InverterModelSpec:
        """Gets an InverterModelSpec instance to describe the active power address or addresses."""
        return self._get_addresses(lambda config: config.active_power)

    def get_work_mode_options_map(self) -> dict[int, str] | None:
        """Gets a select options map for the native work mode register."""
        options_map: dict[int, str] | None = None
        for address_config in self.register_types.values():
            if address_config.work_mode is None or address_config.work_mode_map is None:
                continue

            current_options_map = {
                address_config.work_mode_map[WorkMode.SELF_USE]: "Self Use",
                address_config.work_mode_map[WorkMode.FEED_IN_FIRST]: "Feed-in First",
                address_config.work_mode_map[WorkMode.BACK_UP]: "Back-up",
            }

            if 4 in address_config.work_mode_map.values():
                current_options_map[4] = "Peak Shaving"

            if options_map is None:
                options_map = current_options_map
            else:
                assert options_map == current_options_map, (
                    f"{self}: inconsistent native work mode maps across register types"
                )

        return options_map


@dataclass
class ModbusRemoteControlFactory:
    """
    Factory which creates various things required to define and specify the remote control functionality

    This is used to create the entities which visualise the various bits of the remote control
    (enable, etc), and also the ModbusRemoteControlAddressConfig which is used internally when
    interacting with the remote control
    """

    def __init__(self, addresses: list[RemoteControlAddressSpec]) -> None:
        self.address_specs = addresses

        all_models = [x.get_all_models() for x in addresses]

        def _set_charge_power(manager: EntityRemoteControlManager, value: int) -> None:
            manager.charge_power = -value

        charge_power = ModbusRemoteControlNumberDescription(
            key="force_charge_power",
            name="Desired Force Charge Power",
            models=all_models,
            native_max_value_callback=lambda x: -x.inverter_capacity,  # - to counteract -ve scale
            mode=NumberMode.BOX,
            device_class=NumberDeviceClass.POWER,
            native_min_value=0.0,
            # Max value is read from the inverter
            native_step=0.001,
            native_unit_of_measurement="kW",
            # The register is negative
            scale=-0.001,
            signed=True,
            value_source="user",
            value_note="User-set target used by remote control logic; not read back from a dedicated Modbus register.",
            value_setter=_set_charge_power,
        )

        def _set_discharge_power(manager: EntityRemoteControlManager, value: int) -> None:
            manager.discharge_power = -value

        # hass type hints are messed up, and mypy doesn't see inherited dataclass properties on the EntityDescriptions
        discharge_power = ModbusRemoteControlNumberDescription(
            key="force_discharge_power",
            name="Desired Force Discharge Power",
            models=all_models,
            native_max_value_callback=lambda x: -x.inverter_capacity,  # - to counteract -ve scale
            mode=NumberMode.BOX,
            device_class=NumberDeviceClass.POWER,
            native_min_value=0.0,
            # Max value is read from the inverter
            native_step=0.001,
            native_unit_of_measurement="kW",
            scale=-0.001,
            signed=True,
            value_source="user",
            value_note="User-set target used by remote control logic; not read back from a dedicated Modbus register.",
            value_setter=_set_discharge_power,
        )

        # Models without a work_mode address get one of these
        remote_control_select = ModbusRemoteControlSelectDescription(
            key="force_charge_mode",
            name="Force Charge Mode",
            models=[x.get_models_without_work_mode() for x in self.address_specs],
        )

        native_remote_control_select = ModbusEnumSensorDescription(
            key="remote_control_native",
            name="Remote Control (Native)",
            address=[x.get_remote_enable_address() for x in self.address_specs],
            options_map={0: "Disabled", 1: "Enabled"},
        )

        native_remote_control_power = ModbusSensorDescription(
            key="remote_control_power_native",
            name="Remote Control Power (Native)",
            addresses=[x.get_active_power_addresses() for x in self.address_specs],
            device_class=SensorDeviceClass.POWER,
            state_class=SensorStateClass.MEASUREMENT,
            native_unit_of_measurement="kW",
            scale=0.001,
            signed=True,
        )

        native_work_mode_selects = []
        for address_spec in self.address_specs:
            options_map = address_spec.get_work_mode_options_map()
            if options_map is None:
                continue

            native_work_mode_selects.append(
                ModbusEnumSensorDescription(
                    key="work_mode_native",
                    name="Work Mode (Native)",
                    address=[address_spec.get_work_mode_address()],
                    options_map=options_map,
                )
            )

        def _set_max_soc(manager: EntityRemoteControlManager, value: int) -> None:
            manager.max_soc = value

        # Models without max_soc get one of these
        force_charge_max_soc = ModbusRemoteControlNumberDescription(
            key="force_charge_max_soc",
            name="Desired Force Charge Max SoC",
            models=[x.get_models_without_max_soc() for x in self.address_specs],
            native_min_value=0.0,
            native_max_value_callback=lambda _x: 100,
            mode=NumberMode.BOX,
            device_class=NumberDeviceClass.BATTERY,
            # Max value is read from the inverter
            native_step=1,
            native_unit_of_measurement="%",
            icon="mdi:battery-arrow-up",
            value_source="user",
            value_note="User override used when inverter Max SoC register is unavailable for this model.",
            value_getter=lambda manager: manager.max_soc,
            value_setter=_set_max_soc,
        )

        def _set_export_limit(manager: EntityRemoteControlManager, value: int) -> None:
            # This 'value' is what comes from the UI (likely in Watts if scaled by 0.001)
            manager.export_limit = value

        # Updated filter to include the Smart 15 (H3_SMART)
        relevant_models = [
            x.get_all_models() for x in self.address_specs
            if x.models & (Inv.KH_SET | Inv.H3_SET | Inv.H3_SMART | Inv.H3_PRO_SET)
        ]

        export_limit = ModbusRemoteControlNumberDescription(
            key="export_limit",
            name="Export Limit",
            models=relevant_models,
            native_max_value_callback=lambda x: 60000,
            mode=NumberMode.BOX,
            device_class=NumberDeviceClass.POWER,
            native_min_value=0.0,
            native_step=0.001,
            native_unit_of_measurement="kW",
            scale=0.001,
            value_source="modbus",
            value_note="Displayed in kW in UI; Modbus register value is read/written in W.",
            value_getter=lambda manager: manager.export_limit,
            value_setter=_set_export_limit,
        )

        self.entity_descriptions: list[EntityFactory] = [
            native_remote_control_select,
            native_remote_control_power,
            *native_work_mode_selects,
            export_limit,
            charge_power,
            discharge_power,
            remote_control_select,
            force_charge_max_soc,
        ]

    def create_if_supported(
        self,
        _controller: EntityController,
        inverter_model: Inv,
        register_type: RegisterType,
    ) -> ModbusRemoteControlAddressConfig | None:
        """
        If the inverter model / connection type supports a charge period, fetches a ModbusChargePeriodAddressConfig
        containing the register addresses involved. If not supported, returns None.
        """

        result: ModbusRemoteControlAddressConfig | None = None
        for address_spec in self.address_specs:
            if inverter_model in address_spec.models:
                address_config = address_spec.register_types.get(register_type)
                if address_config is not None:
                    assert result is None, (
                        f"{self}: multiple remote control addresses defined for ({inverter_model}, {register_type})"
                    )

                    result = address_config
        return result
