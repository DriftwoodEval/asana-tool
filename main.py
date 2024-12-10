import multiprocessing

# Bizarrely, this needs to be up here for native mode, don't move it
multiprocessing.set_start_method("spawn", force=True)
import re
import time
from os import getenv
from typing import Any, Dict, Optional

import asana
import keyring
from asana.rest import ApiException
from dotenv import load_dotenv
from nicegui import ui

load_dotenv()


class AsanaClient:
    def __init__(self) -> None:
        self.config: Dict[str, Optional[str]] = {
            "token": None,
            "workspace": None,
            "initials": None,
        }
        self.allowed_domains = ["mhs.com", "pearsonassessments.com"]
        self.projects_api = None
        self.cached_projects = None
        self.last_Fetch_time = None
        self.cache_duration = 300  # 5 minutes
        self.load_config()

    def load_config(self) -> None:
        """Load configuration from environment or keyring"""
        for key in self.config:
            self.config[key] = getenv(f"ASANA_{key.upper()}") or keyring.get_password(
                "asana", key
            )

        if all(self.config.values()):
            self._init_asana()

    def _init_asana(self) -> None:
        """Initialize Asana client"""
        configuration = asana.Configuration()
        configuration.access_token = self.config["token"]  # type: ignore
        self.projects_api = asana.ProjectsApi(asana.ApiClient(configuration))

    def save_config(self, key: str, value: str) -> None:
        """Save configuration to keyring and update client"""
        keyring.set_password("asana", key, value)
        self.config[key] = value
        if all(self.config.values()):
            self._init_asana()

    @property
    def is_configured(self) -> bool:
        """Check if all required configuration is present"""
        return all(self.config.values())

    async def fetch_projects(
        self, opt_fields="name,color,permalink_url,notes,created_at"
    ) -> list[dict] | None:
        current_time = time.time()

        # If we have cached data that's still fresh, return it
        if (
            self.cached_projects is not None
            and self.last_fetch_time is not None
            and current_time - self.last_fetch_time < self.cache_duration
        ):
            return self.cached_projects

        # Otherwise fetch new data
        if not self.is_configured or not self.projects_api:
            return None

        opts: dict[str, Any] = {
            "limit": 100,
            "archived": False,
            "opt_fields": opt_fields,
        }
        try:
            print("Fetching fresh projects data...")
            api_response: list[dict] = list(
                self.projects_api.get_projects_for_workspace(
                    self.config["workspace"],
                    opts,  # type: ignore
                )
            )
            self.cached_projects = api_response
            self.last_fetch_time = current_time
            print(f"{len(api_response)} projects found.")
            return api_response

        except ApiException as e:
            print(
                f"Exception when calling ProjectsApi->get_projects_for_workspace: {e}"
            )
            return None

    def filter_projects(
        self, color: str | None = None, with_dates: bool = False
    ) -> list[dict]:
        if not self.cached_projects:
            return []

        filtered = self.cached_projects

        if color:
            filtered = [p for p in filtered if p["color"] == color]

        if with_dates:
            filtered = [
                p
                for p in filtered
                if re.search(r"\d{1,2}.\d{1,2}(.\d{1,4})?", p["name"])
            ]

        return filtered

    def fetch_project(
        self,
        project_gid: str,
        opt_fields: str = "name,color,permalink_url,notes,created_at",
    ) -> dict | None:
        """Fetch the latest version of a single project by its GID"""
        if not self.is_configured or not self.projects_api:
            return None

        try:
            return self.projects_api.get_project(
                project_gid,
                opt_fields=opt_fields,  # type: ignore
            )
        except ApiException as e:
            print(f"Exception when calling ProjectsApi->get_project: {e}")
            return None


def create_app():
    client = AsanaClient()

    async def refresh_projects():
        await client.fetch_projects()
        ui.notify("Projects refreshed", type="positive")

    ui.timer(300000, refresh_projects)  # Five minutes in ms

    async def display_projects(
        color: str | None = None,
        with_dates: bool = False,
        sort_by_creation: bool = False,
    ):
        """Reusable function to display filtered projects as a list"""
        spinning = ui.spinner(type="dots", size="xl")

        projects = await client.fetch_projects()
        if not projects:
            spinning.visible = False
            ui.label("No projects found")
            return

        filtered_projects = client.filter_projects(color, with_dates)

        if sort_by_creation:
            filtered_projects.sort(key=lambda p: p["created_at"])

        spinning.visible = False
        for project in filtered_projects:
            ui.link(project["name"], project["permalink_url"], new_tab=True)

    def show_settings(force: bool = False):
        with ui.dialog() as dialog, ui.card().classes("w-96"):
            ui.label("Asana Settings").classes("text-xl font-bold")
            dialog.props("persistent")
            inputs = {
                key: ui.input(key.title(), value=client.config[key] or "").classes(
                    "w-full"
                )
                for key in client.config
            }

            async def save_settings():
                for key, input_field in inputs.items():
                    if not input_field.value:
                        ui.notify("All fields are required!", type="negative")
                        return
                    client.save_config(key, input_field.value)

                if client.is_configured:
                    ui.notify("Settings saved successfully!", type="positive")
                    dialog.close()
                    ui.navigate.reload()
                else:
                    ui.notify("Invalid configuration!", type="negative")

            with ui.row():
                ui.button("Save", on_click=save_settings).props("icon=save")
                if not force:
                    ui.button("Cancel", on_click=dialog.close).props("icon=close")

        dialog.open()

    def create_header():
        with ui.header().classes("items-center justify-between"):
            ui.link("Asana Tool", "/").classes(
                "text-xl font-bold text-white no-underline"
            )
            ui.button(on_click=show_settings, icon="settings").props("flat color=white")

    @ui.page("/")
    def root():
        create_header()
        ui.label("Get list of:").classes("text-lg")
        ui.link("all", "/all")
        ui.button(
            "Purples with dates", on_click=lambda: ui.navigate.to("/purple-dates")
        ).classes("!bg-[#CD95EA]")
        ui.button(
            "Yellows with dates", on_click=lambda: ui.navigate.to("/yellow-dates")
        ).classes("!bg-[#F8DF72]")
        ui.button(
            "Orange with dates", on_click=lambda: ui.navigate.to("/orange-dates")
        ).classes("!bg-[#EC8D71]")

        if not client.is_configured:
            ui.timer(0.1, lambda: show_settings(force=True), once=True)

    @ui.page("/all")
    async def all_projects():
        create_header()
        ui.label("All projects")
        spinning = ui.spinner(type="dots", size="xl")
        await ui.context.client.connected()

        projects = await client.fetch_projects()

        if projects:
            spinning.visible = False
            colors_seen = set()
            for project in projects:
                color = project["color"]
                if color not in colors_seen:
                    colors_seen.add(color)
                    ui.label(
                        f"Color: {color} (Example: {project['name']} at {project['permalink_url']})"
                    )

    @ui.page("/purple-dates")
    async def purple_dates():
        create_header()
        ui.label("Purples with dates").classes("text-lg")
        await display_projects(color="light-purple", with_dates=True)

    @ui.page("/yellow-dates")
    async def yellow_dates():
        create_header()
        ui.label("Yellows with dates (sorted by creation)").classes("text-lg")
        await display_projects(
            color="dark-brown", with_dates=True, sort_by_creation=True
        )

    @ui.page("/orange-dates")
    async def orange_dates():
        create_header()
        ui.label("Oranges with dates").classes("text-lg")
        await display_projects(color="dark-orange", with_dates=True)

    ui.run(native=True, title="Asana Tool")


create_app()
