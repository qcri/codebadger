/*
 * cmdline.c - Command line processing
 * 
 * Parses and dispatches shell commands entered interactively or
 * read from a script file.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include "../include/utils.h"

/* Command buffer sizes */
#define CMD_BUFFER_SIZE 512
#define MAX_ARGS 32

/*
 * Read a line of input from stdin into a heap-allocated buffer.
 * Strips the trailing newline.  Caller must free the returned pointer.
 */
char *cmdline_read_input(void)
{
    char *buffer = malloc(CMD_BUFFER_SIZE);
    if (!buffer) {
        return NULL;
    }
    
    if (fgets(buffer, CMD_BUFFER_SIZE, stdin) == NULL) {
        free(buffer);
        return NULL;
    }
    
    /* Remove trailing newline */
    size_t len = strlen(buffer);
    if (len > 0 && buffer[len - 1] == '\n') {
        buffer[len - 1] = '\0';
    }
    
    return buffer;
}

/*
 * Read the contents of filepath into a heap-allocated buffer.
 * Caller must free the returned pointer.
 */
char *cmdline_read_from_file(const char *filepath)
{
    FILE *fp = fopen(filepath, "r");
    if (!fp) {
        return NULL;
    }
    
    char *buffer = malloc(CMD_BUFFER_SIZE);
    if (!buffer) {
        fclose(fp);
        return NULL;
    }
    
    size_t n = fread(buffer, 1, CMD_BUFFER_SIZE - 1, fp);
    buffer[n] = '\0';
    fclose(fp);
    
    return buffer;
}

/*
 * Strip shell metacharacters from input, keeping only alphanumeric
 * characters, spaces, and basic punctuation.
 */
char *cmdline_sanitize(const char *input)
{
    if (!input) {
        return NULL;
    }
    
    size_t len = strlen(input);
    char *sanitized = malloc(len + 1);
    if (!sanitized) {
        return NULL;
    }
    
    size_t j = 0;
    for (size_t i = 0; i < len; i++) {
        char c = input[i];
        /* Only allow alphanumeric, space, and basic punctuation */
        if (isalnum(c) || c == ' ' || c == '.' || c == '-' || c == '_') {
            sanitized[j++] = c;
        }
        /* Skip shell metacharacters: ; | & $ ` etc */
    }
    sanitized[j] = '\0';
    
    return sanitized;
}

/*
 * Pass cmd directly to the shell without further processing.
 */
int cmdline_execute_unsafe(const char *cmd)
{
    if (!cmd) {
        return ERR_INVALID_PARAM;
    }
    
    return system(cmd);
}

/*
 * Sanitize cmd before passing it to the shell.
 */
int cmdline_execute_safe(const char *cmd)
{
    if (!cmd) {
        return ERR_INVALID_PARAM;
    }
    
    char *sanitized = cmdline_sanitize(cmd);
    if (!sanitized) {
        return ERR_OUT_OF_MEMORY;
    }
    
    int result = system(sanitized);
    
    free(sanitized);
    return result;
}

/*
 * Append user_arg to base_cmd and execute the resulting string.
 */
int cmdline_build_and_execute(const char *base_cmd, const char *user_arg)
{
    if (!base_cmd || !user_arg) {
        return ERR_INVALID_PARAM;
    }
    
    char full_cmd[CMD_BUFFER_SIZE];
    
    snprintf(full_cmd, sizeof(full_cmd), "%s %s", base_cmd, user_arg);
    
    return system(full_cmd);
}

/*
 * Parse command into arguments
 */
int cmdline_parse_args(const char *cmdline, char **argv, int max_args)
{
    if (!cmdline || !argv || max_args <= 0) {
        return 0;
    }
    
    char *copy = safe_strdup(cmdline);
    if (!copy) {
        return 0;
    }
    
    int argc = 0;
    char *saveptr;
    char *token = strtok_r(copy, " \t", &saveptr);
    
    while (token && argc < max_args - 1) {
        argv[argc++] = safe_strdup(token);
        token = strtok_r(NULL, " \t", &saveptr);
    }
    argv[argc] = NULL;
    
    free(copy);
    return argc;
}

/*
 * Free parsed arguments
 */
void cmdline_free_args(char **argv, int argc)
{
    if (!argv) {
        return;
    }
    
    for (int i = 0; i < argc; i++) {
        if (argv[i]) {
            free(argv[i]);
        }
    }
}

/*
 * Open a pipe to cmd and return the read end for capturing output.
 */
FILE *cmdline_popen_unsafe(const char *cmd)
{
    if (!cmd) {
        return NULL;
    }
    
    return popen(cmd, "r");
}

/*
 * Display prompt, read one command from stdin, and execute it.
 */
int cmdline_interactive_execute(const char *prompt)
{
    printf("%s", prompt ? prompt : "> ");
    fflush(stdout);
    
    char *input = cmdline_read_input();
    if (!input) {
        return ERR_IO_ERROR;
    }
    
    int result = cmdline_execute_unsafe(input);
    
    free(input);
    return result;
}

/*
 * Read filepath line by line and execute each non-comment line as a
 * shell command.
 */
int cmdline_process_command_file(const char *filepath)
{
    if (!filepath) {
        return ERR_INVALID_PARAM;
    }
    
    char *content = cmdline_read_from_file(filepath);
    if (!content) {
        return ERR_IO_ERROR;
    }
    
    /* Process each line as a command */
    char *saveptr;
    char *line = strtok_r(content, "\n", &saveptr);
    
    while (line) {
        /* Skip empty lines and comments */
        if (line[0] != '\0' && line[0] != '#') {
            cmdline_execute_unsafe(line);
        }
        line = strtok_r(NULL, "\n", &saveptr);
    }
    
    free(content);
    return ERR_SUCCESS;
}

/*
 * Format data into buf using the caller-supplied format string.
 */
static void format_user_message(char *buf, size_t size, 
                                const char *format, const char *data)
{
    sprintf(buf, format, data);
}

/*
 * Format user_data according to user_format and print the result.
 */
int cmdline_format_message(const char *user_format, const char *user_data)
{
    if (!user_format) {
        return ERR_INVALID_PARAM;
    }
    
    char buffer[CMD_BUFFER_SIZE];
    
    format_user_message(buffer, sizeof(buffer), user_format, 
                       user_data ? user_data : "");
    
    printf("%s\n", buffer);
    return ERR_SUCCESS;
}
