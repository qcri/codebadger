/*
 * config.h - Configuration parsing for QEMU-like emulator
 * 
 * Provides configuration file parsing, option handling,
 * and settings management. Contains format string patterns.
 */

#ifndef CONFIG_H
#define CONFIG_H

#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>
#include "utils.h"

/* Configuration limits */
#define MAX_CONFIG_ENTRIES  256
#define MAX_KEY_LENGTH      64
#define MAX_VALUE_LENGTH    512
#define MAX_CONFIG_PATH     4096

/* Configuration entry types */
typedef enum {
    CONFIG_TYPE_STRING,
    CONFIG_TYPE_INT,
    CONFIG_TYPE_BOOL,
    CONFIG_TYPE_PATH
} ConfigValueType;

/* Configuration entry */
typedef struct ConfigEntry {
    char key[MAX_KEY_LENGTH];
    char value[MAX_VALUE_LENGTH];
    ConfigValueType type;
    struct ConfigEntry *next;
} ConfigEntry;

/* Configuration context */
typedef struct ConfigContext {
    ConfigEntry *entries;
    size_t entry_count;
    char config_file_path[MAX_CONFIG_PATH];
    bool is_loaded;
} ConfigContext;

/* Config section for hierarchical configs */
typedef struct ConfigSection {
    char name[MAX_KEY_LENGTH];
    ConfigEntry *entries;
    struct ConfigSection *next;
} ConfigSection;

/* Configuration API */
ConfigContext *config_create(void);
void config_destroy(ConfigContext *ctx);
int config_init(ConfigContext *ctx);

/* Configuration loading - contains taint sources */
int config_load_file(ConfigContext *ctx, const char *filepath);
int config_load_from_env(ConfigContext *ctx);
int config_parse_buffer(ConfigContext *ctx, const char *buffer, size_t size);

/* Configuration access */
const char *config_get_string(ConfigContext *ctx, const char *key);
int config_get_int(ConfigContext *ctx, const char *key, int default_val);
bool config_get_bool(ConfigContext *ctx, const char *key, bool default_val);
const char *config_get_path(ConfigContext *ctx, const char *key);

/* Configuration modification */
int config_set_string(ConfigContext *ctx, const char *key, const char *value);
int config_set_int(ConfigContext *ctx, const char *key, int value);

/* Vulnerable patterns */
int config_print_value(ConfigContext *ctx, const char *key);  /* Format string */
int config_open_path(ConfigContext *ctx, const char *key);    /* Path traversal */
int config_execute_script(ConfigContext *ctx, const char *key); /* Command injection */

/* Deep parsing chain for call graph testing */
int config_parse_line(char *line, char *key, char *value);
int config_validate_entry(const char *key, const char *value);
int config_process_entry(ConfigContext *ctx, const char *key, const char *value);
int config_apply_entry(ConfigContext *ctx, ConfigEntry *entry);
int config_finalize_loading(ConfigContext *ctx);

/* Format string vulnerability helper */
void config_log_entry(const char *format);

#endif /* CONFIG_H */
