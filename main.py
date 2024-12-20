import asyncio
import multiprocessing
from datetime import date, datetime

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
            },
            "dark-purple": {
                "name": "dark-purple",
                "color": "#9E97E7",
            },
            "yellow": {
                "name": "dark-brown",
                "color": "#F8DF72",
            },
            "orange": {
                "name": "dark-orange",
                "color": "#EC8D71",
            },
            "blue": {
                "name": "light-blue",
                "color": "#4573D2",
            },
            "light-blue": {
                "name": "dark-teal",
                "color": "#9EE7E3",
            },
            "light-teal": {
                "name": "light-teal",
                "color": "#4ECBC4",
            },
            "coral": {"name": "light-red", "color": "#FC979A"},
            "hot-pink": {
                "name": "dark-pink",
                "color": "#F26FB2",
            },
            "light-pink": {
                "name": "light-pink",
                "color": "#F9AAEF",
            },
        }
        self.page_configs = {
            "andrew": {
                "title": "Andrew",
                "colors": ["blue"],
                "types": ["list", "review"],
                "users": ["AJP"],
            },
            "babynet": {
                "title": "BabyNet",
                "colors": ["orange"],
                "types": ["list"],
            },
            "barbara": {
                "title": "Barbara and New Referrals",
                "colors": ["yellow"],
                "types": ["list"],
            },
            "deadlines": {
                "title": "Deadlines",
                "colors": ["purple", "dark-purple"],
                "types": ["list"],
                "with_dates": True,
            },
            "ifsp": {
                "title": "IFSP Purples",
                "colors": ["purple", "dark-purple"],
                "types": ["list"],
                "ifsp_only": True,
            },
            "insurance": {
                "title": "Insurance",
                "colors": ["hot-pink", "light-pink"],
                "types": ["list"],
            },
            "needs-scheduling": {
                "title": "Needs to Be Scheduled",
                "colors": ["purple", "dark-purple"],
                "types": ["list"],
            },
            "questionnaires": {
                "title": "Questionnaires",
                "colors": ["light-blue", "coral"],
                "types": ["list", "review"],
            },
            "other": {
                "title": "Other Projects",
                "colors": [],
                "types": ["list"],
                "is_other": True,
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

    def is_on_hold(self, project: dict) -> bool:
        if not project.get("notes"):
            return False

        user_initials = self.config.get("initials")
        if not user_initials:
            return False

        notes = project["notes"]
        hold_pattern = re.compile(
            r"(?:.*?\s)?hold\s+(\d{1,2}/\d{1,2}(?:/\d{2,4})?)\s+[/]*(\w+)[/]*"
        )

        for match in hold_pattern.finditer(notes):
            date_str, initials = match.groups()

            if initials.upper() != user_initials.upper():
                continue

            try:
                hold_date = None
                # Handle different date formats
                if "/" in date_str:
                    if date_str.count("/") == 1:
                        hold_date = (
                            datetime.strptime(date_str, "%m/%d")
                            .replace(year=date.today().year)
                            .date()
                        )
                    else:
                        # Handle 2 or 4 digit years
                        try:
                            hold_date = datetime.strptime(date_str, "%m/%d/%Y").date()
                        except ValueError:
                            hold_date = datetime.strptime(date_str, "%m/%d/%y").date()

                if hold_date is not None:
                    # If hold date is today or in the future, project should be hidden
                    return hold_date >= date.today()

            except ValueError:
                print(f"Invalid date format in hold entry: {date_str}")
                continue

        return False

    def has_ifsp(self, project: dict) -> bool:
        if not project.get("notes"):
            return False

        return bool(re.search(r"\bIFSP\b", project["notes"].upper()))

    def filter_projects(
        self,
        projects: list[dict],
        colors: list[str] | str | None = None,
        with_dates: bool = False,
        ifsp_only: bool = False,
        is_other: bool = False,
    ) -> tuple[list[dict], int]:
        if not projects:
            return [], 0

        held_projects = sum(1 for p in projects if self.is_on_hold(p))
        filtered = [p for p in projects if not self.is_on_hold(p)]

        if is_other:
            used_colors = set()
            for config in self.page_configs.values():
                if config.get("colors") and not config.get("is_other"):
                    for color in config["colors"]:
                        used_colors.add(self.colors[color]["name"])

            filtered = [p for p in filtered if p["color"] not in used_colors]
        elif colors:
            internal_colors = [self.colors[c]["name"] for c in colors]
            filtered = [p for p in filtered if p["color"] in internal_colors]

        if with_dates:
            filtered = [
                p
                for p in filtered
                if re.search(r"\d{1,2}.\d{1,2}(.\d{1,4})?", p["name"])
            ]

        if ifsp_only:
            filtered = [p for p in filtered if self.has_ifsp(p)]

        return filtered, held_projects

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
            new_note += " ///" + initials

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
        if is_initialized or not client.is_configured:
            return is_initialized

        result = await client.fetch_projects()
        is_initialized = result is not None
        return is_initialized

    async def display_projects(
        title: str,
        colors: str | list[str] | None = None,
        with_dates: bool = False,
        sort_by_creation: bool = False,
        ifsp_only: bool = False,
        is_other: bool = False,
    ):
        page_title = ui.label(f"{title}").classes("text-lg")

        projects = await client.fetch_projects()
        if not projects:
            ui.label("No projects found")
            return

        filtered_projects, held_count = client.filter_projects(
            projects, colors, with_dates, ifsp_only, is_other
        )

        if sort_by_creation:
            filtered_projects.sort(key=lambda p: p["created_at"])

        def open_all_links():
            for project in filtered_projects:
                ui.navigate.to(project["permalink_url"], new_tab=True)

        if len(filtered_projects) <= 10:
            ui.button("Open All Links in Asana", on_click=open_all_links).classes(
                "mb-4"
            )

        title_text = f"{title} ({len(filtered_projects)})"
        if held_count > 0:
            title_text += f" [{held_count} on hold]"
        page_title.set_text(title_text)

        for project in filtered_projects:
            link_text = (
                f"{project['name']} ({project['color']})"
                if is_other
                else project["name"]
            )
            ui.link(link_text, project["permalink_url"], new_tab=True)

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

    async def refresh_projects():
        await client.fetch_projects(force=True)
        ui.navigate.reload()

    timeout_warning = False

    @ui.refreshable
    def display_cache_warning():
        nonlocal timeout_warning
        if not client.last_fetch_time or timeout_warning:
            return

        elapsed_seconds = int(time.time() - client.last_fetch_time)
        if elapsed_seconds >= client.cache_duration:
            timeout_warning = True
            ui.notify(
                "Data is possibly out-of-date.",
                position="top",
                type="warning",
                timeout=0,
            )

        else:
            return

    def create_header(refresh=True, root_page=False):
        with ui.header().classes("items-center justify-between"):
            ui.link("Asana Tool", "/").classes(
                "text-xl font-bold text-white no-underline"
            )
            with ui.row().classes("items-center"):
                if refresh:
                    display_staleness()
                    ui.button(
                        on_click=lambda: refresh_projects(),
                        icon="refresh",
                    ).props("flat color=white")
                if root_page:
                    ui.button(on_click=show_settings, icon="settings").props(
                        "flat color=white"
                    )

        if refresh:
            display_cache_warning()

        ui.timer(1.0, display_staleness.refresh)
        ui.timer(1.0, display_cache_warning.refresh)

    def create_colored_button(title: str, colors: list[str], on_click: Callable):
        button = ui.button(title, on_click=on_click)

        def get_button_style(colors):
            if isinstance(colors, list):
                if len(colors) > 1:
                    return f"!bg-gradient-to-r from-[{client.colors[colors[0]]['color']}] to-[{client.colors[colors[1]]['color']}] !text-black"
                return f"!bg-[{client.colors[colors[0]]['color']}] !text-black"
            return f"!bg=[{client.colors[colors]['colors']}] !text-black"

        button.classes(get_button_style(colors))
        return button

    def should_show_button(config, user_initials, hide_buttons):
        if not hide_buttons:
            return True
        return "users" in config and user_initials in config.get("users", [])

    @ui.page("/")
    def root():
        create_header(root_page=True)

        loading = create_loading_overlay()

        content_container = ui.element("div")
        content_container.set_visibility(False)
        with content_container:
            with ui.row():
                user_initials = client.config.get("initials")
                hide_buttons = (
                    any(
                        "users" in config and user_initials in config["users"]
                        for config in client.page_configs.values()
                    )
                    if user_initials
                    else False
                )

                with ui.column():
                    ui.label("List:").classes("text-lg")

                    for config_key, config in client.page_configs.items():
                        if not should_show_button(config, user_initials, hide_buttons):
                            continue

                        if "list" in config.get("types", []):
                            if config.get("is_other"):
                                ui.button(
                                    config["title"],
                                    on_click=lambda k=config_key: ui.navigate.to(
                                        f"/list/{k}"
                                    ),
                                ).classes("!bg-gray-400 !text-black")
                            else:
                                create_colored_button(
                                    config["title"],
                                    config["colors"],
                                    lambda k=config_key: ui.navigate.to(f"/list/{k}"),
                                )

                with ui.column():
                    ui.label("Review:").classes("text-lg")
                    for config_key, config in client.page_configs.items():
                        if not should_show_button(config, user_initials, hide_buttons):
                            continue

                        if "review" in config.get("types", []):
                            if config.get("is_other"):
                                ui.button(
                                    config["title"],
                                    on_click=lambda k=config_key: ui.navigate.to(
                                        f"/review/{k}"
                                    ),
                                ).classes("!bg-gray-400 !text-black")
                            else:
                                create_colored_button(
                                    config["title"],
                                    config["colors"],
                                    lambda k=config_key: ui.navigate.to(f"/review/{k}"),
                                )

        async def init():
            nonlocal loading, content_container, is_initialized
            if not client.is_configured:
                ui.timer(0.1, lambda: show_settings(force=True), once=True)
                return

            if is_initialized:
                await asyncio.sleep(
                    0.1
                )  # Kind of jank but I've fought with this for too long and this works
                loading.visible = False
                content_container.visible = True
                return

            await initialize_app()

        ui.timer(0.1, init)

    @ui.page("/list/{config_key}")
    async def list_colors(config_key: str):
        create_header()
        config = client.page_configs.get(config_key)

        if not config:
            ui.label("Invalid configuration").classes("text-lg")
            return

        if config.get("is_other"):
            await display_projects(
                title=config["title"],
                colors=[],
                with_dates=config.get("with_dates", False),
                ifsp_only=config.get("ifsp_only", False),
                is_other=True,
            )
        else:
            await display_projects(
                title=config["title"],
                colors=config["colors"],
                with_dates=config.get("with_dates", False),
                ifsp_only=config.get("ifsp_only", False),
            )

    @ui.page("/review/{config_key}")
    async def review_projects(config_key: str):
        create_header(refresh=False)
        config = client.page_configs.get(config_key)
        if not config:
            ui.label("Invalid configuration").classes("text-lg")
            return

        ui.label(f"Reviewing {config['title']}").classes("text-lg")

        projects = await client.fetch_projects()
        if not projects:
            ui.label("No projects found")
            return

        filtered_projects, held_count = client.filter_projects(
            projects,
            colors=config["colors"],
            with_dates=config.get("with_dates", False),
            ifsp_only=config.get("ifsp_only", False),
        )

        if not filtered_projects:
            ui.label("No projects found.")
            return

        if held_count > 0:
            ui.label(
                f"[{held_count} {'project' if held_count == 1 else 'projects'} on hold]"
            ).classes("text-sm text-gray-500")

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

                def add_hold():
                    with ui.dialog() as dialog, ui.card().classes("w-96"):
                        hold_input = (
                            ui.date(mask="MM/DD/YY")
                            .classes("w-full")
                            .props(
                                ':options="date => { const today = new Date(); today.setHours(0,0,0,0); return new Date(date) > today; }"'
                            )
                        )

                        def submit():
                            if hold_input.value:
                                client.add_note(
                                    f"hold {hold_input.value}",
                                    project["gid"],
                                )
                                dialog.close()
                                go_next()

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

                    ui.button(
                        "Hold",
                        on_click=add_hold,
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
