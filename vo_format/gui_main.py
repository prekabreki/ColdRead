"""GUI entry point for ColdRead."""

from vo_format.gui import VOFormatterApp


def gui_main():
    app = VOFormatterApp()
    app.mainloop()


if __name__ == "__main__":
    gui_main()
