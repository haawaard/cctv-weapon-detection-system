"""Small, interruptible UI animations; never animate evidence or layout geometry."""
from PySide6.QtCore import QEasingCurve, QPoint, QRectF, QSize, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QDialogButtonBox,
    QGraphicsOpacityEffect, QPushButton, QStyle, QStyleOptionButton,
    QStyleOptionTab, QStylePainter, QTabBar)
from shiboken6 import isValid

from mockup_ui.preferences import load_preferences
from mockup_ui.ui_theme import current_theme


def accent():
    return QColor("#9db9ee" if current_theme() == "dark" else "#22396f")


def enabled():
    return not load_preferences().reduce_motion


def animate(owner, start, end, update, duration=420, *, channel="main", spring=True):
    motions = getattr(owner, "_motions", None)
    if motions is None:
        motions = owner._motions = {}
    previous = motions.get(channel)
    if previous is not None:
        previous.stop()
        previous.deleteLater()
    animation = QVariantAnimation(owner)
    owner._motion = animation
    motions[channel] = animation
    animation.setStartValue(start)
    animation.setEndValue(end)
    animation.setDuration(duration if enabled() else 0)
    curve = QEasingCurve(QEasingCurve.Type.OutBack if spring else QEasingCurve.Type.OutCubic)
    if spring:
        curve.setOvershoot(2.15)
    animation.setEasingCurve(curve)
    animation.valueChanged.connect(update)
    animation.start()
    if not enabled():
        update(end)
    return animation


class SpringButton(QPushButton):
    """Paint a press contraction without moving the hit target or its layout."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._inset = 0.0
        self._hover = 0.0
        self.pressed.connect(lambda: animate(self, self._inset, 3.5, self._set_inset, 110, spring=False))
        self.released.connect(lambda: animate(self, max(self._inset, 2.8), 0.0, self._set_inset))

    def _set_hover(self, value):
        self._hover = float(value)
        self.update()

    def enterEvent(self, event):
        if self.isEnabled():
            animate(self, self._hover, 1.0, self._set_hover, 180, channel="hover", spring=False)
        super().enterEvent(event)

    def leaveEvent(self, event):
        animate(self, self._hover, 0.0, self._set_hover, 240, channel="hover", spring=False)
        super().leaveEvent(event)

    def hideEvent(self, event):
        for animation in getattr(self, "_motions", {}).values():
            animation.stop()
        self._inset = self._hover = 0.0
        super().hideEvent(event)

    def _set_inset(self, value):
        self._inset = float(value)
        self.update()

    def paintEvent(self, event):
        painter = QStylePainter(self)
        option = QStyleOptionButton()
        self.initStyleOption(option)
        # Fractional painting makes the spring visible while hit targets stay fixed.
        scale = 1 - self._inset / max(self.height(), 1)
        painter.translate(self.width() / 2, self.height() / 2)
        painter.scale(scale, scale)
        painter.translate(-self.width() / 2, -self.height() / 2)
        painter.drawControl(QStyle.ControlElement.CE_PushButton, option)
        if self._hover and self.isEnabled():
            color = accent()
            color.setAlpha(round(25 * self._hover))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), 10, 10)


class SpringSwitch(QCheckBox):
    """Native checkbox semantics with a sliding switch indicator."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._position = float(self.isChecked())
        self._keyboard_focus = False
        self.toggled.connect(self._toggle)

    def focusInEvent(self, event):
        self._keyboard_focus = event.reason() in (
            Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason,
            Qt.FocusReason.ShortcutFocusReason,
        )
        super().focusInEvent(event)
        self.update()

    def mousePressEvent(self, event):
        self._keyboard_focus = False
        super().mousePressEvent(event)
        self.update()

    def _toggle(self, checked):
        target = float(checked)
        if not self.isVisible():
            self._set_position(target)
        else:
            animate(self, self._position, target, self._set_position)

    def _set_position(self, value):
        self._position = float(value)
        self.update()

    def sizeHint(self):
        size = super().sizeHint()
        return QSize(size.width() + 35, max(size.height(), 34))

    def minimumSizeHint(self):
        return self.sizeHint()

    def hitButton(self, point):
        return self.rect().contains(point)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(.45)
        y = (self.height() - 26) / 2
        painter.setPen(QColor(0, 0, 0, 0))
        if self.hasFocus() and self._keyboard_focus:
            halo = accent()
            halo.setAlpha(45)
            painter.setBrush(halo)
            painter.drawRoundedRect(QRectF(0, y - 3, 51, 32), 16, 16)
        painter.setBrush(accent() if self.isChecked() else
                         QColor("#354c70" if current_theme() == "dark" else "#c4cfe0"))
        painter.drawRoundedRect(QRectF(1, y, 46, 26), 13, 13)
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(QRectF(4 + 20 * self._position, y + 3, 20, 20))
        painter.setPen(self.palette().windowText().color())
        text_rect = self.rect().adjusted(57, 0, 0, 0)
        text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, text_rect.width())
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter, text)


class SpringTabs(QTabBar):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._indicator = QRectF()
        self.currentChanged.connect(self._move_indicator)

    def _set_indicator(self, rect):
        self._indicator = rect
        self.update()

    def _move_indicator(self, index):
        target = QRectF(self.tabRect(index))
        if self._indicator.isNull() or not self.isVisible():
            self._set_indicator(target)
        else:
            animate(self, self._indicator, target, self._set_indicator, 520)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self, "_motion", None):
            self._motion.stop()
        self._indicator = QRectF(self.tabRect(self.currentIndex()))

    def paintEvent(self, event):
        painter = QStylePainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Native labels preserve keyboard focus and disabled states; the pill moves.
        for index in range(self.count()):
            option = QStyleOptionTab()
            self.initStyleOption(option, index)
            painter.drawControl(QStyle.ControlElement.CE_TabBarTabShape, option)
        color = accent()
        if self.objectName() == "modeTabs":
            color = QColor("#22396f")
        else:
            color.setAlpha(42)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(self._indicator.adjusted(4, 4, -4, -4), 11, 11)
        for index in range(self.count()):
            option = QStyleOptionTab()
            self.initStyleOption(option, index)
            painter.drawControl(QStyle.ControlElement.CE_TabBarTabLabel, option)


def show_settings_motion(dialog):
    """Keep the native dialog, animate its entrance inside the screen bounds."""
    if not isValid(dialog) or not dialog.isVisible() or not enabled():
        return
    target = dialog.pos()
    dialog._motion_target = target
    available = dialog.screen().availableGeometry()
    travel = max(0, min(64, available.bottom() - dialog.frameGeometry().bottom()))
    start = target + QPoint(0, travel)
    dialog.move(start)
    animate(dialog, start, target, dialog.move, 560)
    if QApplication.platformName() != "offscreen":
        dialog.setWindowOpacity(.25)
        animate(dialog, .25, 1.0, dialog.setWindowOpacity, 280, channel="opacity", spring=False)


class SpringDialog(QDialog):
    """Shared entrance for application dialogs, including configuration and review."""
    def showEvent(self, event):
        super().showEvent(event)
        # Defer until Qt has placed the native window at its final screen position.
        QTimer.singleShot(0, lambda: show_settings_motion(self))
        for box in self.findChildren(QDialogButtonBox):
            for button in box.buttons():
                if not button.property("motionConnected"):
                    button.setProperty("motionConnected", True)
                    button.pressed.connect(lambda b=button: reveal_feedback(b))

    def hideEvent(self, event):
        for animation in getattr(self, "_motions", {}).values():
            animation.stop()
        if hasattr(self, "_motion_target"):
            self.move(self._motion_target)
        if QApplication.platformName() != "offscreen":
            self.setWindowOpacity(1)
        super().hideEvent(event)


def finish_motion():
    """Applying Reduce motion also settles transitions already in progress."""
    for widget in QApplication.allWidgets():
        for animation in getattr(widget, "_motions", {}).values():
            animation.setCurrentTime(animation.duration())
            animation.stop()
        progress = getattr(widget, "_progress_animation", None)
        if progress is not None:
            progress.setCurrentTime(progress.duration())
            progress.stop()
        timer = getattr(widget, "_animation_timer", None)
        if timer is not None:
            if enabled() and widget.isVisible():
                timer.start()
            else:
                timer.stop()


def reveal_feedback(label):
    if not enabled():
        return
    effect = label.graphicsEffect()
    if effect is None:
        effect = QGraphicsOpacityEffect(label)
        label.setGraphicsEffect(effect)
    if isinstance(effect, QGraphicsOpacityEffect):
        animate(label, .2, 1.0, effect.setOpacity, 420, spring=False)
