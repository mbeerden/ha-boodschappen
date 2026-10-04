"""Todo-lijst per Grocy-boodschappenlijst (Lidl, Jumbo, ...)."""
from __future__ import annotations

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import GrocyError
from .const import DOMAIN
from .coordinator import BoodschappenCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: BoodschappenCoordinator = entry.runtime_data
    known: set[int] = set()

    @callback
    def _add_new_lists() -> None:
        new = [lid for lid in coordinator.data.lists if lid not in known]
        if new:
            known.update(new)
            async_add_entities(WinkelLijst(coordinator, entry, lid) for lid in new)

    _add_new_lists()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_lists))


class WinkelLijst(CoordinatorEntity[BoodschappenCoordinator], TodoListEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:cart-outline"
    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
        | TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM
    )

    def __init__(self, coordinator: BoodschappenCoordinator, entry: ConfigEntry, list_id: int) -> None:
        super().__init__(coordinator)
        self._list_id = list_id
        name = coordinator.data.lists[list_id]["name"]
        self._attr_unique_id = f"{entry.entry_id}_lijst_{list_id}"
        self._attr_name = name
        self.entity_id = f"todo.boodschappen_{name.lower().replace(' ', '_')}"

    @property
    def available(self) -> bool:
        return super().available and self._list_id in self.coordinator.data.lists

    @property
    def todo_items(self) -> list[TodoItem]:
        return [
            TodoItem(
                uid=str(i.id),
                summary=i.summary,
                status=TodoItemStatus.COMPLETED if i.done else TodoItemStatus.NEEDS_ACTION,
                description=i.description,
            )
            for i in self.coordinator.data.items_for_list(self._list_id)
        ]

    @property
    def extra_state_attributes(self) -> dict:
        items = self.coordinator.data.items_for_list(self._list_id)
        return {
            "grocy_lijst_id": self._list_id,
            "items": [
                {
                    "id": i.id,
                    "naam": i.name,
                    "aantal": i.amount,
                    "vak": i.group_name,
                    "notitie": i.note,
                    "klaar": i.done,
                }
                for i in items
            ],
        }

    async def _run(self, coro) -> None:
        try:
            await coro
        except GrocyError as err:
            raise HomeAssistantError(f"Grocy: {err}") from err
        finally:
            # direct verversen (niet vertraagd), zodat de lijst meteen klopt
            await self.coordinator.async_refresh()

    async def async_create_todo_item(self, item: TodoItem) -> None:
        await self._run(self.coordinator.add_text(self._list_id, item.summary or "", item.description))

    async def async_update_todo_item(self, item: TodoItem) -> None:
        item_id = int(item.uid)
        current = next((i for i in self.coordinator.data.items if i.id == item_id), None)
        if current is None:
            raise HomeAssistantError("Item bestaat niet meer")

        async def _apply() -> None:
            if item.summary and item.summary != current.summary:
                await self.coordinator.rename(item_id, item.summary)
            if item.status is not None:
                await self.coordinator.set_done(item_id, item.status == TodoItemStatus.COMPLETED)

        await self._run(_apply())

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        await self._run(self.coordinator.delete_items([int(u) for u in uids]))
