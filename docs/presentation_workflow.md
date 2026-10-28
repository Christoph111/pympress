# Presentation workflow

Pympress separates the audience's **Content** window from the **Presenter** console. The file list can be docked in the Presenter or used as an independent window. Playback controls are independent and nonmodal; moving them does not move the presentation windows. Closing either management window hides it so it can be opened again.

## Windows and keyboard controls

In the default shortcuts below, **Primary** means **Command (⌘)** on macOS and **Ctrl** on other platforms. A shortcut explicitly labelled **Ctrl** uses the Control key, including on macOS. Custom keyboard bindings can change these defaults.

The presentation shortcuts in this table apply when the **Presenter** or **Content** window has focus. The management windows keep their keyboard input local so that searching a list or editing timings cannot trigger presentation commands.

| Action | Default control |
| --- | --- |
| Show or hide the file list | **Shift+L**, **Primary+Shift+L**, the **File list** button, or **File → File list…**. The explicit **Ctrl+Shift+L** shortcut remains available. |
| Dock or undock the file list | **Primary+Shift+D**, the **Dock / Undock** button, or **File → Dock file list in Presenter** |
| Toggle the persistent laser pointer | **L** |
| Next file | **Primary+Right** or **Primary+Down**; **Alt+PageDown** is also available. |
| Previous file | **Primary+Left** or **Primary+Up**; **Alt+PageUp** is also available. |
| Quit with confirmation | **Primary+Q**; **Q** is also available in presentation windows. |
| Open playback controls | **Playback** in the toolbar, or **Primary+Shift+P** |
| Start, pause, or resume automatic playback | **F8** |
| Stop automatic playback | **F9** |
| Pause or resume active automatic playback | **P** |
| Cancel the current operation or leave fullscreen | **Escape**, as described below |

On keyboards where function keys control media or system functions, hold **Fn** when using F8 or F9.

**Escape** first cancels active input, drawing, or zoom modes. If automatic playback is active, it stops playback and leaves fullscreen; otherwise it leaves fullscreen.

In the **file list**, **Shift+L** hides the window, **L** toggles the laser, and **Primary+Left/Up** or **Primary+Right/Down** changes files. These navigation and laser shortcuts leave focused text inputs alone. **Primary+V** pastes files or file paths. Use the Presenter or Content window for F8, F9, P, and other presentation shortcuts.

In the **file list** and **Playback** windows, **Escape** only hides that management window; playback and fullscreen remain unchanged. In a file chooser, Escape cancels the chooser.

**Primary+Q**, **File → Quit**, and closing a presentation window ask **Quit Pympress?**
Press **Enter** to confirm. **Space** or **Escape** cancels, even when the OK button
has focus. The dialog defaults to **Cancel**. Primary+Q also works while editing in
the file list, Playback, Output, or a file chooser. Any unsaved PDF changes are still
handled after confirming. Command-line **--quit** and SIGINT retain their existing
unsaved-work checks without this additional confirmation.

When the persistent laser is off, manual pointing remains available. Hold the left mouse button and move over the **current-slide preview** in the Presenter window; the corresponding position appears in the Content window. Releasing the button, leaving the preview, losing focus, or changing files hides the manual pointer. In the Content window, use **Ctrl+left mouse button** for manual pointing. Ordinary clicks on PDF links remain available.

## Arrange the Presenter console

Open **Fields** to show or hide the current and next previews, notes slide, annotations, slide number, timer, target duration, and clock. Font sizes can be changed separately. Resize the slide previews by dragging their dividers.

Enable **Edit layout**, then drag a field title. Central presentation fields exchange positions; the information fields along the bottom can be reordered. Highlighted titles identify the active drag handles. **Advanced layout…** also provides nested arrangements, orientation, and controls for whether sections can be resized using dividers. These preferences are saved in the normal Pympress configuration.

## Manage PDFs, images, and file lists

The same list can contain PDFs and PNG, JPEG, BMP, TIFF, and GIF images. Each image is one slide; GIF uses its first frame.

Use **Add files…**, paste local file paths with **Paste** or **Primary+V**, or drag one or more files into a presentation window or the file list. Text paths can be supplied one per line. Double-click a list entry to display it. **Remove** deletes the list entry without deleting the original file.

Use **Move up** and **Move down** to arrange the presentation order. **Save list…** saves the order, file display times, individual slide timings, and playback settings together as JSON. **Load list…** restores a saved list; older list versions are still accepted. Missing files remain visible and are skipped during automatic playback. Playback stops on unreadable files or when unsaved PDF changes prevent moving to another file.

**Continue to next file by default** sets what happens when Next reaches the last
slide of a file. The **Next file** checkbox beside each filename shows its effective
choice. Clicking it sets an individual override; select rows and choose **Reset to
global** to make them follow the default again. Explicit **Next file** keyboard
commands remain available regardless of these checkboxes. Both the default and
individual choices are saved in the file list. Older lists retain continuation.

At a file boundary, **Next slide** previews the first page of the next available
file and displays its filename and page number. Turning continuation off clears
that preview. With several next-slide fields, remaining pages of the current file
are followed by pages of one upcoming file. PDF previews show the complete source
page; image previews show the same image used during presentation. An unavailable
preview is labelled accordingly. Previewing never activates the file or follows
its PDF links.

Automatic **Entire file list** playback also respects each file's continuation
choice, including when looping. **Current file** playback stays within that file;
its next preview follows the current-file loop setting while playback is active.

## Configure timing and transitions

Open **Playback**, then:

1. Choose **Current file** or **Entire file list**, and enable **Loop** if required. Starting playback begins at the current slide.
2. Set **Time per slide** for the active file. For an image, this is the display time of that image.
3. To give individual PDF slides different timings, double-click their display-time cells. Clear a cell, or use **Reset selected slide to file default**, to restore the file's default timing.
4. Choose **Cut** or **Crossfade**. The slider and numeric input share the same transition duration, from **0 to 5 seconds**. Slide timing starts after the crossfade. **Use transitions for this file** can disable transitions for an individual file; when moving between files, the incoming file determines whether a transition is used.
5. Select **Apply**, then **Start**. Pausing also freezes an in-progress crossfade. Manual navigation while paused shows the selected slide immediately and keeps playback paused.

Unapplied changes remain available when the active file changes. **Apply** edits the file originally selected for editing; **Discard** reloads settings for the file that is now active.

Crossfades affect the audience's Content window. The Presenter preview immediately shows the new slide so that the operator can see the selection. If resource limits prevent a crossfade, the transition falls back to a cut.

## Output modes and video backgrounds

Open **Output** in the Presenter toolbar, **Presentation → Output settings…**, or
**Primary+Shift+O**. Select a **Display**, choose a mode, then select **Apply and show**:

- **Window** displays the ordinary audience window with its title bar.
- **Fullscreen** uses the operating system's native fullscreen mode.
- **Borderless overlay** covers the selected display without a title bar or a native
  fullscreen/Space transition. It fades the entire audience window over whatever is
  underneath, for example a background video running in another application.

**Overlay fade** sets the full-range fade time from **0 to 5 seconds**, independently
of slide and file crossfades. The output is fully opaque when shown and fully hidden
when faded out; there is no persistent partial-opacity mode. Reversing an unfinished
fade continues from its current opacity at the same speed. **Show** and **Fade out**
operate on the applied configuration; choosing a different mode or display remains a
draft until **Apply and show**.

In the Presenter or Content window, **F10** or **Show / hide** toggles output. In overlay
mode, the ordinary content-fullscreen key (**F**) also toggles output instead of entering
a native fullscreen Space. **Escape** retains its input/drawing cancellation priority,
then fades out a visible overlay; it keeps the selected overlay mode available for the
next Show. Escape inside the Output settings window only closes those settings.

On macOS, output uses public native window opacity and Space properties so it can
accompany fullscreen applications. Application-owned windows disable automatic native
tab grouping and retain independent placement. The overlay does not take operator focus.
Keep the background video on the chosen display; verify the exact VLC/Spaces arrangement
before a live presentation. A mapped native test validates the alpha endpoints and display
coverage, but does not establish every external application's fullscreen behavior.

The display selection and fade time are stored in the normal application configuration.
A remembered overlay starts hidden. If its saved display is unavailable, choose an available
display explicitly; output does not automatically move onto the remaining operator display.
Disconnecting an active output display hides output immediately. Wayland cannot supply the
required per-window positioning/opacity; X11 requires desktop compositing.

## Dock the file list

Open **File list**, then choose **Dock** in its header. The same list moves into a
left-hand panel beside the Presenter previews. Drag the divider to change its
width; toolbar and timer controls remain available. Buttons wrap in narrow docks,
and the Folder column is hidden while file names and missing-file status remain
visible. Selection, file order and scroll position survive changing hosts.

Choose **Undock** to return to an independent window. **Shift+L** and **Hide** show
or hide the selected host; **Primary+Shift+D** changes hosts. Mode, visibility and
dock width are remembered at the next launch. Closing the application preserves
these preferences.

When focus is inside the dock, typing, list navigation and **Escape** stay local;
Escape hides the list before it affects the presentation. Clicking a Presenter
preview returns keyboard control to the presentation. Audience-window keys retain
their normal slide/output behavior. File dialogs belong to the visible host, and
dropping files anywhere in the list appends them without interrupting the current
slide.
