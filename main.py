import asyncio
import multiprocessing
from datetime import datetime

# Bizarrely, this needs to be up here for native mode, don't move it
multiprocessing.set_start_method("spawn", force=True)
import re
import time
from os import getenv
from typing import Any, Callable, Dict, Optional

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
        self.last_fetch_time = None
        self.cache_duration = 300  # 5 minutes
        self.colors = {
            "purple": {
                "name": "light-purple",
                "color": "#CD95EA",
                "include_dates": True,
            },
            "dark-purple": {
                "name": "dark-purple",
                "color": "#9E97E7",
                "include_dates": False,
            },
            "yellow": {
                "name": "dark-brown",
                "color": "#F8DF72",
                "include_dates": False,
            },
            "orange": {
                "name": "dark-orange",
                "color": "#EC8D71",
                "include_dates": True,
            },
            "blue": {"name": "light-blue", "color": "#4573D2", "include_dates": False},
            "light-blue": {
                "name": "dark-teal",
                "color": "#9EE7E3",
                "include_dates": False,
            },
            "light-teal": {
                "name": "light-teal",
                "color": "#4ECBC4",
                "include_dates": False,
            },
            "coral": {"name": "light-red", "color": "#FC979A", "include_dates": False},
            "hot-pink": {
                "name": "dark-pink",
                "color": "#F26FB2",
                "include_dates": False,
            },
            "light-pink": {
                "name": "light-pink",
                "color": "#F9AAEF",
                "include_dates": False,
            },
        }
        self.page_configs = {
            "questionnaires": {
                "title": "Questionnaires",
                "colors": ["light-blue", "coral"],
                "types": ["list", "review"],
                "with_dates": False,
            },
            "andrew": {
                "title": "Andrew",
                "colors": ["blue"],
                "types": ["list", "review"],
                "with_dates": False,
                "users": ["AJP"],
            },
            "insurance": {
                "title": "Insurance",
                "colors": ["hot-pink", "light-pink"],
                "types": ["list"],
                "with_dates": False,
            },
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
        if key == "initials":
            value = value.upper()
        keyring.set_password("asana", key, value)
        self.config[key] = value
        if all(self.config.values()):
            self._init_asana()

    @property
    def is_configured(self) -> bool:
        """Check if all required configuration is present"""
        return all(self.config.values())

    async def fetch_projects(
        self, opt_fields="name,color,permalink_url,notes,created_at", force=False
    ) -> list[dict] | None:
        current_time = time.time()

        if (
            not force
            and self.cached_projects is not None
            and self.last_fetch_time is not None
            and current_time - self.last_fetch_time < self.cache_duration
        ):
            return self.cached_projects

        if not self.is_configured or not self.projects_api:
            return None

        opts: dict[str, Any] = {
            "limit": 100,
            "archived": False,
            "opt_fields": opt_fields,
        }

        max_retries = 3
        retry_delay = 1
        all_projects = []

        for attempt in range(max_retries):
            try:
                print("Fetching fresh projects data...")
                api_response = self.projects_api.get_projects_for_workspace(
                    self.config["workspace"],
                    opts,
                )

                # Collect all projects from pagination
                for project in api_response:  # type: ignore
                    all_projects.append(project)

                self.cached_projects = all_projects
                self.last_fetch_time = current_time
                print(f"{len(all_projects)} projects found.")
                return all_projects

            except ApiException as e:
                if e.status == 503 and attempt < max_retries - 1:
                    print(f"Got 503 error, retrying in {retry_delay} seconds...")
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                    continue
                print(
                    f"Exception when calling ProjectsApi->get_projects_for_workspace: {e}"
                )
                return None

    def filter_projects(
        self,
        projects: list[dict],
        colors: list[str] | str | None = None,
        with_dates: bool = False,
    ) -> list[dict]:
        if not projects:
            return []

        filtered = projects

        if colors:
            if isinstance(colors, str):
                colors = [colors]
            filtered = [p for p in filtered if p["color"] in colors]

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
                opts={"opt_fields": opt_fields},  # type: ignore
            )
        except ApiException as e:
            print(f"Exception when calling ProjectsApi->get_project: {e}")
            return None

    def replace_notes(self, new_note: str, project_gid: str):
        if not self.is_configured or not self.projects_api:
            return None

        body = {"data": {"notes": new_note}}
        try:
            self.projects_api.update_project(
                body, project_gid, opts={"opt_fields": "name, notes"}
            )
            return "Note added."
        except ApiException as e:
            return f"Exception when calling ProjectsApi->update_project: {e}"

    def change_color(self, new_color: str, project_gid: str):
        if not self.is_configured or not self.projects_api:
            return None

        internal_color = self.colors.get(new_color)
        if not internal_color:
            return f"Invalid color: {new_color}"
        internal_color = internal_color["name"]

        body = {"data": {"color": internal_color}}
        try:
            self.projects_api.update_project(
                body, project_gid, opts={"opt_fields": "name, color"}
            )
            return f"Color changed to {new_color}."
        except ApiException as e:
            return f"Exception when calling ProjectsApi->update_project: {e}"

    def add_note(self, new_note: str, project_gid: str):
        if not self.is_configured or not self.projects_api:
            return None

        today_str = datetime.now().strftime("%m/%d")
        new_note = today_str + " " + new_note
        initials = self.config.get("initials")
        if initials:
            new_note += " " + initials

        current_project: dict[str, str] | None = self.fetch_project(project_gid)
        if current_project:
            current_notes = current_project.get("notes", "")
            new_notes: str = new_note + "\n" + current_notes
            self.replace_notes(new_notes, project_gid)


def create_app():
    client = AsanaClient()
    is_initialized = False

    def create_loading_overlay():
        overlay = ui.element("div").classes(
            "fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50"
        )
        with overlay:
            with ui.column().classes("flex items-center justify-center"):
                ui.spinner(size="xl", color="white")
                ui.label("Fetching projects...").classes("text-white")
        return overlay

    async def initialize_app():
        nonlocal is_initialized
        if is_initialized:
            return True
        if client.is_configured and not is_initialized:
            await client.fetch_projects()
            is_initialized = True

    async def display_projects(
        colors: str | list[str] | None = None,
        with_dates: bool = False,
        sort_by_creation: bool = False,
    ):
        projects = await client.fetch_projects()
        if not projects:
            ui.label("No projects found")
            return

        filtered_projects = client.filter_projects(projects, colors, with_dates)

        if sort_by_creation:
            filtered_projects.sort(key=lambda p: p["created_at"])

        def open_all_links():
            for project in filtered_projects:
                ui.navigate.to(project["permalink_url"], new_tab=True)

        if len(filtered_projects) <= 10:
            ui.button("Open All Links in Asana", on_click=open_all_links).classes(
                "mb-4"
            )

        for project in filtered_projects:
            ui.link(project["name"], project["permalink_url"], new_tab=True)

    def show_settings(force: bool = False):
        with ui.dialog() as dialog, ui.card().classes("w-96"):
            ui.label("Tool Settings").classes("text-xl font-bold")
            dialog.props("persistent")

            ui.link(
                "Get an Asana API token for your account",
                "https://app.asana.com/0/my-apps",
                True,
            ).classes("text-sm")

            ui.link(
                "Get your workspace GID",
                "https://app.asana.com/api/1.0/workspaces?opt_pretty",
                True,
            ).classes("text-sm")

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
                    await client.fetch_projects()
                    dialog.close()
                    ui.navigate.reload()
                else:
                    ui.notify("Invalid configuration!", type="negative")

            with ui.row():
                ui.button("Save", on_click=save_settings).props("icon=save")
                if not force:
                    ui.button("Cancel", on_click=dialog.close).props("icon=close")

        dialog.open()

    @ui.refreshable
    def display_staleness():
        if client.last_fetch_time is None:
            return ui.label("No data loaded").classes("text-white")

        elapsed_seconds = int(time.time() - client.last_fetch_time)

        if elapsed_seconds < 60:
            time_text = f"{elapsed_seconds}s"
        elif elapsed_seconds < 3600:
            minutes = elapsed_seconds // 60
            time_text = f"{minutes}m"
        else:
            hours = elapsed_seconds // 3600
            time_text = f"{hours}h"

        return ui.label(f"Data age: {time_text}").classes("text-white")

    def create_header(refresh=True, root_page=False):
        with ui.header().classes("items-center justify-between"):
            ui.link("Asana Tool", "/").classes(
                "text-xl font-bold text-white no-underline"
            )
            with ui.row().classes("items-center"):
                if refresh:
                    display_staleness()
                    ui.button(
                        on_click=lambda: client.fetch_projects(force=True),
                        icon="refresh",
                    ).props("flat color=white")
                if root_page:
                    ui.button(on_click=show_settings, icon="settings").props(
                        "flat color=white"
                    )

        # Update the staleness display every second
        ui.timer(1.0, display_staleness.refresh)

    def create_colored_button(title: str, colors: list[str], on_click: Callable):
        button = ui.button(title, on_click=on_click)

        def get_button_style(color_list):
            if len(color_list) > 1:
                return f"!bg-gradient-to-r from-[{client.colors[color_list[0]]['color']}] to-[{client.colors[color_list[1]]['color']}] !text-black"
            return f"!bg-[{client.colors[color_list[0]]['color']}] !text-black"

        button.classes(get_button_style(colors))
        return button

    def validate_colors(
        colors: str,
    ) -> tuple[dict[str, str | list[str] | bool] | None, list[str], str]:
        color_list = colors.split(",")
        internal_colors = []

        matching_config = None
        for config in client.page_configs.values():
            if sorted(color_list) == sorted(config["colors"]):
                matching_config = config
                break

        if matching_config:
            page_title = matching_config["title"]
        else:
            page_title = ", ".join(c.capitalize() + "s" for c in color_list)

        for color in color_list:
            internal_color = client.colors.get(color)
            if not internal_color:
                raise ValueError(f"Invalid color: {color}")
            internal_colors.append(internal_color["name"])

        return matching_config, internal_colors, page_title

    @ui.page("/")
    def root():
        create_header(root_page=True)

        loading = create_loading_overlay()

        content_container = ui.element("div")
        content_container.set_visibility(False)
        with content_container:
            with ui.row():
                user_initials = client.config.get("initials")

                hide_buttons = False
                if user_initials:
                    for config in client.page_configs.values():
                        if "users" in config and user_initials in config["users"]:
                            hide_buttons = True
                            break
                with ui.column():
                    ui.label("List:").classes("text-lg")

                    for config_key, config in client.page_configs.items():
                        if hide_buttons:
                            if "users" not in config or user_initials not in config.get(
                                "users", []
                            ):
                                continue

                        if "list" in config.get("types", []):
                            create_colored_button(
                                config["title"],
                                config["colors"],
                                lambda p=",".join(config["colors"]): ui.navigate.to(
                                    f"/list/{p}"
                                ),
                            )

                with ui.column():
                    ui.label("Review:").classes("text-lg")
                    for config_key, config in client.page_configs.items():
                        if hide_buttons:
                            if "users" not in config or user_initials not in config.get(
                                "users", []
                            ):
                                continue
                        if "review" in config.get("types", []):
                            button = ui.button(
                                config["title"],
                                on_click=lambda p=",".join(
                                    config["colors"]
                                ): ui.navigate.to(f"/review/{p}"),
                            )

                            # Apply button styling based on configured colors
                            def get_button_style(color_list):
                                if len(color_list) > 1:
                                    return f"!bg-gradient-to-r from-[{client.colors[color_list[0]]['color']}] to-[{client.colors[color_list[1]]['color']}] !text-black"
                                return f"!bg-[{client.colors[color_list[0]]['color']}] !text-black"

                            button.classes(get_button_style(config["colors"]))

        async def init():
            nonlocal loading
            if not client.is_configured:
                ui.timer(0.1, lambda: show_settings(force=True), once=True)
                return

            try:
                await initialize_app()
            finally:
                loading.set_visibility(False)
                content_container.set_visibility(True)

        ui.timer(0.2, init)

    @ui.page("/list/{colors}")
    async def list_colors(colors: str):
        create_header()
        matching_config, internal_colors, page_title = validate_colors(colors)

        ui.label(f"{page_title} (click to open in Asana)").classes("text-lg")

        if matching_config:
            await display_projects(
                colors=internal_colors, with_dates=bool(matching_config["with_dates"])
            )

    @ui.page("/review/{colors}")
    async def review_projects(colors: str):
        create_header(refresh=False)
        matching_config, internal_colors, page_title = validate_colors(colors)

        ui.label(f"Reviewing {page_title}").classes("text-lg")

        if matching_config:
            projects = await client.fetch_projects()
            if not projects:
                ui.label("No projects found")
                return

            filtered_projects = client.filter_projects(
                projects,
                internal_colors,
                with_dates=bool(matching_config["with_dates"]),
            )

            if not filtered_projects:
                ui.label("No projects found.")
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
                        ui.label("Notes:").classes("font-bold")
                        ui.label(project["notes"]).classes("whitespace-pre-wrap")

                    def add_note():
                        with ui.dialog() as dialog, ui.card().classes("w-96"):
                            note_input = ui.input("Enter note").classes("w-full")

                            def submit():
                                if note_input.value:
                                    client.add_note(note_input.value, project["gid"])
                                    dialog.close()

                            with ui.row():
                                ui.button("Submit", on_click=submit)
                                ui.button("Cancel", on_click=dialog.close)
                        dialog.open()

                    with ui.row():
                        ui.button(
                            "Open in Asana",
                            on_click=lambda: ui.navigate.to(
                                project["permalink_url"], new_tab=True
                            ),
                        )

                        ui.button(
                            "Add note",
                            on_click=add_note,
                        )

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
