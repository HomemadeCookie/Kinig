#pragma once

enum class LedPattern { Off, Idle, Recording, Processing, ShowQr, Error };

void controlsBegin();
void controlsSetPattern(LedPattern pattern);
void controlsTick();
// True once per press. When PIN_BUTTON is TBD, the letter s on Serial counts.
bool controlsButtonPressed();
