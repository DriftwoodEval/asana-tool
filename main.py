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
        self.color_mapping = {
            "purple": "light-purple",
            "yellow": "dark-brown",
            "orange": "dark-orange",
        }
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
    is_initialized = False

    async def refresh_projects():
        if client.is_configured:
            print("Checking if projects need refreshing...")
            await client.fetch_projects()

    def start_refresh_timer():
        if client.is_configured:
            print("Starting refresh timer.")
            ui.timer(client.cache_duration, refresh_projects)

    async def initialize_app():
        nonlocal is_initialized
        if client.is_configured and not is_initialized:
            await refresh_projects()
            start_refresh_timer()
            is_initialized = True

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
                    start_refresh_timer()
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

        with ui.element("div").classes(
            "w-full flex flex-col items-center justify-center"
        ) as loading_container:
            ui.spinner(size="lg")
            ui.label("Fetching from Asana...").classes("mt-2")

        with ui.element("div").classes("hidden") as content_container:
            with ui.row():
                with ui.column():
                    ui.label("Get list of:").classes("text-lg")
                    ui.link("all (debug page)", "/all")
                    colors = {
                        "purple": "#CD95EA",
                        "yellow": "#F8DF72",
                        "orange": "#EC8D71",
                    }
                    for color, bg in colors.items():
                        ui.button(
                            f"{color.capitalize()}s with dates",
                            on_click=lambda c=color: ui.navigate.to(
                                f"/{c}?with_dates=True"
                            ),
                        ).classes(f"!bg-[{bg}]")

                with ui.column():
                    ui.label("Review:").classes("text-lg")
                    colors = {
                        "purple": "#CD95EA",
                        "yellow": "#F8DF72",
                        "orange": "#EC8D71",
                    }
                    for color, bg in colors.items():
                        ui.button(
                            f"{color.capitalize()}s with dates",
                            on_click=lambda c=color: ui.navigate.to(
                                f"/review/{c}?with_dates=True"
                            ),
                        ).classes(f"!bg-[{bg}]")

        async def init():
            if not client.is_configured:
                ui.timer(0.1, lambda: show_settings(force=True), once=True)
                return

            await initialize_app()
            loading_container.classes(remove="block", add="hidden")
            content_container.classes(remove="hidden", add="block")

        ui.timer(0.1, init)

    @ui.page("/all")
    async def all_projects():
        create_header()
        ui.label("All projects")

        projects = await client.fetch_projects()

        if projects:
            colors_seen = set()
            for project in projects:
                color = project["color"]
                if color not in colors_seen:
                    colors_seen.add(color)
                    ui.label(
                        f"Color: {color} (Example: {project['name']} at {project['permalink_url']})"
                    )

    @ui.page("/{color}")
    async def list_color(color: str, with_dates: bool = False):
        create_header()
        ui.label(
            f"{color.capitalize()}s{" with dates" if with_dates else ""} (click to open in Asana)"
        ).classes("text-lg")
        internal_color = client.color_mapping.get(color)
        if not internal_color:
            ui.label(f"Invalid color: {color}").classes("text-lg text-red-500")
            return
        await display_projects(color=internal_color, with_dates=with_dates)

    @ui.page("/review/{color}")
    async def review_projects(color: str, with_dates: bool = False):
        create_header()
        internal_color = client.color_mapping.get(color)
        if not internal_color:
            ui.label(f"Invalid color: {color}").classes("text-lg text-red-500")
            return
        ui.label(f"Review {color} projects").classes("text-lg")

        filtered_projects = client.filter_projects(internal_color, with_dates)

        if not filtered_projects:
            ui.label(f"No {color} projects found")
            return

        current_index = 0

        @ui.refreshable
        def show_current_project():
            nonlocal current_index
            with ui.card().classes("w-full max-w-2xl mx-auto p-4"):
                if current_index >= len(filtered_projects):
                    ui.label("No more projects to review!").classes("text-xl")
                    return

                project = filtered_projects[current_index]

                ui.label(
                    f"Project {current_index + 1} of {len(filtered_projects)}"
                ).classes("text-sm text-gray-500")
                ui.label(project["name"]).classes("text-xl font-bold")

                if project.get("notes"):
                    ui.label("Notes:").classes("font-bold mt-2")
                    ui.label(project["notes"]).classes("whitespace-pre-wrap")

                ui.link(
                    "Open in Asana", project["permalink_url"], new_tab=True
                ).classes("mt-2")

                with ui.row().classes("w-full justify-between mt-4"):

                    def go_previous():
                        nonlocal current_index
                        current_index = max(0, current_index - 1)
                        show_current_project.refresh()

                    def go_next():
                        nonlocal current_index
                        current_index = min(
                            len(filtered_projects) - 1, current_index + 1
                        )
                        show_current_project.refresh()

                    prev_button = ui.button("Previous", on_click=go_previous).props(
                        "icon=arrow_back"
                    )

                    next_button = ui.button(
                        "Skip/Next",
                        on_click=go_next,
                    ).props("icon=arrow_forward")

                    if current_index <= 0:
                        prev_button.disable()
                    if current_index >= len(filtered_projects) - 1:
                        next_button.disable()

        show_current_project()

    ui.run(native=True, title="Asana Tool")


create_app()
