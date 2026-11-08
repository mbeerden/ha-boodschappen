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
from homeassistant.util import dt as dt_util
from datetime import date

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
    if getattr(coordinator, "klusjes", None) is not None:
        async_add_entities([KlusjesLijst(coordinator, entry)])


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


class KlusjesLijst(TodoListEntity):
    """Open klusjes als to-do-lijst, zodat je ze ook met Assist of in de To-do-weergave ziet en toevoegt."""

    _attr_has_entity_name = True
    _attr_name = "Klusjes"
    _attr_icon = "mdi:broom"
    _attr_should_poll = False
    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
        | TodoListEntityFeature.SET_DUE_DATE_ON_ITEM
        | TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM
    )

    def __init__(self, coordinator: BoodschappenCoordinator, entry: ConfigEntry) -> None:
        self.k = coordinator.klusjes
        self._attr_unique_id = f"{entry.entry_id}_klusjes"
        self.entity_id = "todo.klusjes"

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.k.luister(self.async_write_ha_state))

    @property
    def todo_items(self) -> list[TodoItem]:
        uit = []
        for t in sorted(self.k.open_taken(), key=lambda x: (x.get("datum") or "9999", x["titel"].lower())):
            extra = [x for x in (t.get("persoon"), t.get("ruimte"), "bezig" if t["status"] == "bezig" else None) if x]
            uit.append(TodoItem(
                uid=t["id"], summary=t["titel"], status=TodoItemStatus.NEEDS_ACTION,
                due=date.fromisoformat(t["datum"]) if t.get("datum") else None,
                description=" · ".join(extra) or None,
            ))
        return uit

    @property
    def extra_state_attributes(self) -> dict:
        vandaag = dt_util.now().date().isoformat()
        open_ = self.k.open_taken()
        return {
            "vandaag": sum(1 for t in open_ if t.get("datum") == vandaag),
            "te_laat": sum(1 for t in open_ if t.get("datum") and t["datum"] < vandaag),
            "backlog": sum(1 for t in open_ if t["status"] == "backlog"),
            "gepland": sum(1 for t in open_ if t["status"] == "gepland"),
            "bezig": sum(1 for t in open_ if t["status"] == "bezig"),
        }

    async def async_create_todo_item(self, item: TodoItem) -> None:
        await self._run(self.k.toevoegen({"titel": item.summary, "datum": item.due.isoformat() if isinstance(item.due, date) else None}))

    async def async_update_todo_item(self, item: TodoItem) -> None:
        if item.status == TodoItemStatus.COMPLETED:
            await self._run(self.k.afronden(item.uid))
            return
        velden = {}
        if item.summary:
            velden["titel"] = item.summary
        if isinstance(item.due, date):
            velden["datum"] = item.due.isoformat()
        await self._run(self.k.wijzigen(item.uid, velden))

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        for uid in uids:
            await self._run(self.k.verwijderen(uid))

    async def _run(self, coro) -> None:
        try:
            await coro
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err
