#ifndef CMD_H
#define CMD_H

#include <Arduino.h>
#include <ArduinoJson.h>
#include "head.h"

void wifi_provision_reset();

void handle_cmd(String cmd = "");
void executeCommand(String cmd = "");
void executeFactoryCommand(String cmd = "");

#endif
