import multiprocessing

# Bizarrely, this needs to be up here for native mode, don't move it
multiprocessing.set_start_method("spawn", force=True)
import re
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


def get_asana_projects(
    projects_api, workspace_gid, opt_fields="name,color,permalink_url,notes"
):
    opts: dict[str, Any] = {"limit": 100, "archived": False, "opt_fields": opt_fields}
    try:
        print("Fetching projects...")

        api_response: list[dict] = list(
            projects_api.get_projects_for_workspace(
                workspace_gid,
                opts,  # pyright: ignore (asana api is strange)
            )
        )

    except ApiException as e:
        print(
            "Exception when calling ProjectsApi->get_projects_for_workspace: %s\n" % e
        )
        return

    if api_response:
        print(f"Found {len(api_response)} projects")
        return api_response


def filter_projects_by_color(projects: list[dict], color: str) -> list[dict]:
    filtered_projects: list[dict] = []
    for project in projects:
        if project["color"] != color:
            continue
        filtered_projects.append(project)
    return filtered_projects


def get_projects_with_dates(projects: list[dict]) -> list[dict]:
    filtered_projects: list[dict] = []
    for project in projects:
        if re.search(r"\d{1,2}.\d{1,2}(.\d{1,4})?", project["name"]):
            filtered_projects.append(project)
    return filtered_projects


def create_app():
    client = AsanaClient()

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

        projects = get_asana_projects(client.projects_api, client.config["workspace"])

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
        spinning = ui.spinner(type="dots", size="xl")
        await ui.context.client.connected()

        projects = get_asana_projects(client.projects_api, client.config["workspace"])
        if projects:
            spinning.visible = False
            filtered_projects = filter_projects_by_color(projects, "light-purple")
            filtered_projects = get_projects_with_dates(filtered_projects)
            for project in filtered_projects:
                ui.link(project["name"], project["permalink_url"], True)

    @ui.page("/yellow-dates")
    async def yellow_dates():
        create_header()
        ui.label("Yellows with dates (sorted by creation)").classes("text-lg")
        spinning = ui.spinner(type="dots", size="xl")
        await ui.context.client.connected()

        projects = get_asana_projects(
            client.projects_api,
            client.config["workspace"],
            "name,color,permalink_url,notes,created_at",
        )
        if projects:
            spinning.visible = False
            filtered_projects = filter_projects_by_color(projects, "dark-brown")
            filtered_projects = get_projects_with_dates(filtered_projects)
            filtered_projects = sorted(filtered_projects, key=lambda p: p["created_at"])
            for project in filtered_projects:
                ui.link(project["name"], project["permalink_url"], True)

    @ui.page("/orange-dates")
    async def orange_dates():
        create_header()
        ui.label("Oranges with dates").classes("text-lg")
        spinning = ui.spinner(type="dots", size="xl")
        await ui.context.client.connected()

        projects = get_asana_projects(
            client.projects_api,
            client.config["workspace"],
            "name,color,permalink_url,notes,created_at",
        )
        if projects:
            spinning.visible = False
            filtered_projects = filter_projects_by_color(projects, "dark-orange")
            filtered_projects = get_projects_with_dates(filtered_projects)
            for project in filtered_projects:
                ui.link(project["name"], project["permalink_url"], True)

    ui.run(native=True, title="Asana Tool")


create_app()
