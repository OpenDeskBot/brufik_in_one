/**
 * MIT License — Richard Moore / Project Nayuki (ricmoo/QRCode).
 * Renamed to avoid ESP32 SDK qrcode.h conflict.
 */
#pragma once

#include <stdint.h>

#ifndef __cplusplus
#include <stdbool.h>
#endif

#define MODE_NUMERIC 0
#define MODE_ALPHANUMERIC 1
#define MODE_BYTE 2

#define ECC_LOW 0
#define ECC_MEDIUM 1
#define ECC_QUARTILE 2
#define ECC_HIGH 3

#ifndef LOCK_VERSION
#define LOCK_VERSION 0
#endif

typedef struct QRCode {
  uint8_t version;
  uint8_t size;
  uint8_t ecc;
  uint8_t mode;
  uint8_t mask;
  uint8_t* modules;
} QRCode;

#ifdef __cplusplus
extern "C" {
#endif

uint16_t qrcode_getBufferSize(uint8_t version);

int8_t qrcode_initText(QRCode* qrcode, uint8_t* modules, uint8_t version, uint8_t ecc,
                         const char* data);
int8_t qrcode_initBytes(QRCode* qrcode, uint8_t* modules, uint8_t version, uint8_t ecc,
                         uint8_t* data, uint16_t length);

bool qrcode_getModule(QRCode* qrcode, uint8_t x, uint8_t y);

#ifdef __cplusplus
}
#endif
