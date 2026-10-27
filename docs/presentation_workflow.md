# Presentation workflow

Pympress separates the audience's **Content** window from the **Presenter** console. The file list and playback controls are independent, nonmodal windows: moving or closing them does not move the presentation windows. Closing either management window hides it so it can be opened again.

## Windows and keyboard controls

In the default shortcuts below, **Primary** means **Command (⌘)** on macOS and **Ctrl** on other platforms. A shortcut explicitly labelled **Ctrl** uses the Control key, including on macOS. Custom keyboard bindings can change these defaults.

The presentation shortcuts in this table apply when the **Presenter** or **Content** window has focus. The management windows keep their keyboard input local so that searching a list or editing timings cannot trigger presentation commands.

| Action | Default control |
| --- | --- |
| Show or hide the file list | **Shift+L**, **Primary+Shift+L**, the **File list** button, or **File → File list…**. The explicit **Ctrl+Shift+L** shortcut remains available. |
| Toggle the persistent laser pointer | **L** |
| Next file | **Primary+Right** or **Primary+Down**; **Alt+PageDown** is also available. |
| Previous file | **Primary+Left** or **Primary+Up**; **Alt+PageUp** is also available. |
| Open playback controls | **Playback** in the toolbar, or **Primary+Shift+P** |
| Start, pause, or resume automatic playback | **F8** |
| Stop automatic playback | **F9** |
| Pause or resume active automatic playback | **P** |
| Cancel the current operation or leave fullscreen | **Escape**, as described below |

On keyboards where function keys control media or system functions, hold **Fn** when using F8 or F9.

**Escape** first cancels active input, drawing, or zoom modes. If automatic playback is active, it stops playback and leaves fullscreen; otherwise it leaves fullscreen.

In the **file list**, **Shift+L** hides the window, **L** toggles the laser, and **Primary+Left/Up** or **Primary+Right/Down** changes files. These navigation and laser shortcuts leave focused text inputs alone. **Primary+V** pastes files or file paths. Use the Presenter or Content window for F8, F9, P, and other presentation shortcuts.

In the **file list** and **Playback** windows, **Escape** only hides that management window; playback and fullscreen remain unchanged. In a file chooser, Escape cancels the chooser.

When the persistent laser is off, manual pointing remains available. Hold the left mouse button and move over the **current-slide preview** in the Presenter window; the corresponding position appears in the Content window. Releasing the button, leaving the preview, losing focus, or changing files hides the manual pointer. In the Content window, use **Ctrl+left mouse button** for manual pointing. Ordinary clicks on PDF links remain available.

## Arrange the Presenter console

Open **Fields** to show or hide the current and next previews, notes slide, annotations, slide number, timer, target duration, and clock. Font sizes can be changed separately. Resize the slide previews by dragging their dividers.

Enable **Edit layout**, then drag a field title. Central presentation fields exchange positions; the information fields along the bottom can be reordered. Highlighted titles identify the active drag handles. **Advanced layout…** also provides nested arrangements, orientation, and controls for whether sections can be resized using dividers. These preferences are saved in the normal Pympress configuration.

## Manage PDFs, images, and file lists

The same list can contain PDFs and PNG, JPEG, BMP, TIFF, and GIF images. Each image is one slide; GIF uses its first frame.

Use **Add files…**, paste local file paths with **Paste** or **Primary+V**, or drag one or more files into a presentation window or the file list. Text paths can be supplied one per line. Double-click a list entry to display it. **Remove** deletes the list entry without deleting the original file.

Use **Move up** and **Move down** to arrange the presentation order. **Save list…** saves the order, file display times, individual slide timings, and playback settings together as JSON. **Load list…** restores a saved list; older list versions are still accepted. Missing files remain visible and are skipped during automatic playback. Playback stops on unreadable files or when unsaved PDF changes prevent moving to another file.

## Configure timing and transitions

Open **Playback**, then:

1. Choose **Current file** or **Entire file list**, and enable **Loop** if required. Starting playback begins at the current slide.
2. Set **Time per slide** for the active file. For an image, this is the display time of that image.
3. To give individual PDF slides different timings, double-click their display-time cells. Clear a cell, or use **Reset selected slide to file default**, to restore the file's default timing.
4. Choose **Cut** or **Crossfade**. The slider and numeric input share the same transition duration, from **0 to 5 seconds**. Slide timing starts after the crossfade. **Use transitions for this file** can disable transitions for an individual file; when moving between files, the incoming file determines whether a transition is used.
5. Select **Apply**, then **Start**. Pausing also freezes an in-progress crossfade. Manual navigation while paused shows the selected slide immediately and keeps playback paused.

Unapplied changes remain available when the active file changes. **Apply** edits the file originally selected for editing; **Discard** reloads settings for the file that is now active.

Crossfades affect the audience's Content window. The Presenter preview immediately shows the new slide so that the operator can see the selection. If resource limits prevent a crossfade, the transition falls back to a cut.
